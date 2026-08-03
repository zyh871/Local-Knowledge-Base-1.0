"""
FastAPI application — JWT auth, document management, RAG Q&A endpoints.

Security hardening:
  - CORS restricted to specific origins in production
  - JWT secret from env (config.py validates)
  - File upload size limits
  - Rate limiting with slowapi
  - SSE connection timeout / heartbeat

Structured logging:
  - File rotation (10MB, 30 backups)
  - Request trace ID injection
  - Retrieval performance metrics
"""

import asyncio
import json
import logging
import os
import secrets
import sys
import time as _time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()

import bcrypt
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app import config
from app.agents import ALL_INTENTS, classify as classify_intent
from app.document import (
    SUPPORTED_EXTENSIONS as DOC_SUPPORTED_EXTENSIONS,
    answer_against_text,
    detect_language_hint,
    summarize as long_text_summarize,
    translate as long_text_translate,
)
from app.memory import (
    clear_memory,
    delete_conversation,
    export_conversation,
    get_conversation,
    get_profile,
    list_conversations,
    remember_turn,
    update_profile,
    upsert_conversation,
)
from app.rag_chain import (
    RAGChain,
    add_documents_to_store,
    get_rag_chain,
    get_streaming_rag_chain,
    get_vision_llm,
    load_single_document,
    rebuild_vectorstore,
    remove_document_from_store,
    split_documents,
)
from app.tools.web_search import search as web_search

# ---------------------------------------------------------------------------
#  Structured Logging with Request Tracing
# ---------------------------------------------------------------------------

class TraceIDFilter(logging.Filter):
    def __init__(self, trace_id: str = "-"):
        self.trace_id = trace_id

    def filter(self, record):
        record.trace_id = getattr(record, "trace_id", self.trace_id)
        return True


def setup_logging():
    log_config = config.config.log
    log_dir = config.config.paths.log_dir
    os.makedirs(log_dir, exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
    ]

    if log_config.file_enabled:
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=log_config.max_file_size_mb * 1024 * 1024,
            backupCount=log_config.max_backup_count,
            encoding="utf-8",
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_config.level,
        format=log_config.format,
        datefmt=log_config.datefmt,
        handlers=handlers,
    )

    for handler in logging.getLogger().handlers:
        handler.addFilter(TraceIDFilter())


setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Rate Limiting
# ---------------------------------------------------------------------------

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{config.config.rate_limit.max_requests}/{config.config.rate_limit.window_seconds}seconds"],
    enabled=config.config.rate_limit.enabled,
)

# ---------------------------------------------------------------------------
#  App
# ---------------------------------------------------------------------------

app = FastAPI(title="本地知识库问答系统", version="3.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
CORS_ORIGINS = [
    origin.strip() for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost,http://localhost:80,http://localhost:8081,http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Request Trace ID Middleware ---
class TraceIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())[:8]
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

app.add_middleware(TraceIDMiddleware)

# ---------------------------------------------------------------------------
#  Auth
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not _ADMIN_USERNAME or not _ADMIN_PASSWORD:
    logger.critical("ADMIN_USERNAME 和 ADMIN_PASSWORD 环境变量必须设置！")
    raise RuntimeError("ADMIN_USERNAME 和 ADMIN_PASSWORD 环境变量必须设置")

_ADMIN_HASH = bcrypt.hashpw(_ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

fake_users_db = {
    _ADMIN_USERNAME: {
        "username": _ADMIN_USERNAME,
        "hashed_password": _ADMIN_HASH,
        "disabled": False,
    }
}


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class User(BaseModel):
    username: str
    disabled: Optional[bool] = None


class UserInDB(User):
    hashed_password: str


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes: bytes = plain_password.encode("utf-8")
    hp_bytes: bytes = hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password
    return bcrypt.checkpw(pw_bytes, hp_bytes)


def get_user(db: dict, username: str) -> Optional[UserInDB]:
    user = db.get(username)
    if user:
        return UserInDB(**user)
    return None


def authenticate_user(db: dict, username: str, password: str):
    user = get_user(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)):
    exc = HTTPException(
        status_code=401,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise exc
    except JWTError as e:
        logger.warning(f"JWT token verification failed: {e}")
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    user = get_user(fake_users_db, username)
    if user is None:
        raise exc
    return user


def get_current_active_user(current_user: User = Depends(get_current_user)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return current_user


# ---------------------------------------------------------------------------
#  Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/token", response_model=Token)
@limiter.limit("5/minute")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), request: Request = None):
    user = authenticate_user(fake_users_db, form_data.username, form_data.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {form_data.username}")
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    logger.info(f"User {user.username} logged in successfully")
    return {"access_token": token, "token_type": "bearer"}


@app.get("/users/me/", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user


# ---------------------------------------------------------------------------
#  RAG chain lifecycle
# ---------------------------------------------------------------------------

_rag_chain: Optional[RAGChain] = None


@app.on_event("startup")
async def startup_event():
    global _rag_chain
    logger.info("正在后台预热 RAG 链...")

    def _init():
        chain = get_rag_chain()
        chain.ensure_initialized()
        return chain

    loop = asyncio.get_event_loop()

    async def warmup():
        global _rag_chain
        try:
            _rag_chain = await loop.run_in_executor(None, _init)
            logger.info("RAG 链预热完成")
        except Exception as e:
            logger.warning(f"RAG 链预热失败 (首次请求会延迟初始化): {e}")

    asyncio.ensure_future(warmup())


def _get_chain() -> RAGChain:
    global _rag_chain
    if _rag_chain is None:
        _rag_chain = get_rag_chain()
        _rag_chain.ensure_initialized()
    return _rag_chain


# ---------------------------------------------------------------------------
#  Pydantic models for request/response
# ---------------------------------------------------------------------------

class QuestionRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    force_search: bool = False

    def validate_question(self) -> str:
        q = (self.question or "").strip()
        if not q:
            raise ValueError("问题不能为空")
        if len(q) > 2000:
            raise ValueError("问题长度不能超过2000字符")
        forbidden_patterns = ["<script", "javascript:", "data:", "vbscript:"]
        for p in forbidden_patterns:
            if p in q.lower():
                raise ValueError("问题包含非法内容")
        return q


class QuestionResponse(BaseModel):
    answer: str
    sources: list = []
    data_source: str = "rag"
    trace_id: str = ""
    intent: str = ""
    intent_confidence: float = 0.0
    detected_language: str = "auto"
    needs_clarification: bool = False


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    profession: Optional[str] = None
    language: Optional[str] = None
    facts: Optional[list] = None
    preferences: Optional[dict] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    locked: Optional[bool] = None
    archived: Optional[bool] = None


class LongTextRequest(BaseModel):
    text: str
    operation: str = "summary"
    target_language: Optional[str] = None
    question: Optional[str] = None
    max_chars: int = 600


class FeedbackRequest(BaseModel):
    message_id: str
    conversation_id: str
    rating: int
    comment: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    conversation_ids: List[str]


# ---------------------------------------------------------------------------
#  File upload limits
# ---------------------------------------------------------------------------

MAX_UPLOAD_SIZE = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {ext.lower() for ext in DOC_SUPPORTED_EXTENSIONS}


def _validate_file(file: UploadFile) -> None:
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")
    filename = os.path.basename(file.filename)
    if filename != file.filename:
        raise HTTPException(status_code=400, detail="文件名包含非法字符")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 '{ext}'，请上传: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )


def _save_upload(file: UploadFile, contents: bytes) -> str:
    os.makedirs(config.DATA_PATH, exist_ok=True)
    base_name: str = file.filename or "uploaded_file"
    file_path = os.path.join(config.DATA_PATH, base_name)
    if os.path.exists(file_path):
        base, ext = os.path.splitext(base_name)
        counter = 1
        while os.path.exists(f := os.path.join(config.DATA_PATH, f"{base}_{counter}{ext}")):
            counter += 1
        file_path = f
    with open(file_path, "wb") as f:
        f.write(contents)
    return file_path


# ---------------------------------------------------------------------------
#  Question answering
# ---------------------------------------------------------------------------

@app.post("/ask", response_model=QuestionResponse)
@limiter.limit("30/minute")
async def ask_question(
    req: QuestionRequest,
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    try:
        question = req.validate_question()
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"answer": str(e), "sources": [], "data_source": "error",
                     "trace_id": trace_id, "intent": "error"},
        )
    try:
        logger.info(f"[trace_id={trace_id}] 收到问题: {question[:50]}...")
        chain = _get_chain()
        result = chain.route_invoke(
            {"question": question, "conversation_id": req.conversation_id or "default", "force_search": req.force_search},
            username=current_user.username,
        )
        result["trace_id"] = trace_id
        logger.info(f"[trace_id={trace_id}] 回答完成，data_source={result.get('data_source')}, sources={len(result.get('sources', []))}")
        return result
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] 处理问题失败: {e}", exc_info=True)
        error_type = "unknown"
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            error_type = "timeout"
        elif "token" in str(e).lower() or "auth" in str(e).lower():
            error_type = "auth"
        elif "vector" in str(e).lower() or "chroma" in str(e).lower():
            error_type = "vectorstore"
        elif "file" in str(e).lower() or "format" in str(e).lower():
            error_type = "file"
        return JSONResponse(
            status_code=500,
            content={"answer": f"系统错误: {e}", "sources": [], "data_source": "error",
                     "trace_id": trace_id, "intent": "error", "error_type": error_type},
        )


@app.post("/ask/stream")
@limiter.limit("30/minute")
async def ask_question_stream(
    req: QuestionRequest,
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    try:
        question = req.validate_question()
    except ValueError as e:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': str(e), 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    chain = _get_chain()
    input_data = {"question": question, "conversation_id": req.conversation_id or "default", "force_search": req.force_search}
    username = current_user.username

    async def generate():
        try:
            logger.info(f"[trace_id={trace_id}] 收到流式问题: {question[:50]}...")

            if req.conversation_id and not req.conversation_id.startswith("temp-"):
                try:
                    meta = get_conversation(req.conversation_id)
                    if not meta or not meta.get("title") or meta.get("title") == "新对话":
                        title = question.strip().split("\n")[0][:30]
                        if title:
                            upsert_conversation(
                                req.conversation_id, username=username, title=title, bump_turn=False
                            )
                except Exception:
                    pass

            async for chunk in chain.route_stream(input_data, username=username):
                yield f"data: {json.dumps({**chunk, 'trace_id': trace_id})}\n\n"

        except asyncio.CancelledError:
            logger.info(f"[trace_id={trace_id}] 流式请求被客户端取消")
        except Exception as e:
            logger.error(f"[trace_id={trace_id}] 流式处理失败: {e}", exc_info=True)
            error_type = "unknown"
            if "timeout" in str(e).lower():
                error_type = "timeout"
            elif "token" in str(e).lower():
                error_type = "auth"
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'系统错误: {e}', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'error', 'error_type': error_type}})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".gif"}
MAX_IMAGE_SIZE = 20 * 1024 * 1024


def _image_mime(ext: str) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(ext, "image/jpeg")


@app.post("/ask/image")
@limiter.limit("20/minute")
async def ask_image(
    file: UploadFile = File(...),
    question: str = Form(""),
    conversation_id: str = Form("default"),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    q = (question or "").strip()
    if not q:
        q = "请描述这张图片的内容。"
    if len(q) > 2000:
        q = q[:2000]

    if not file or not file.filename:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': '未收到图片文件', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'不支持的图片格式 {ext}，请上传 jpg/png/bmp/tiff/webp/gif', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    try:
        contents = await file.read()
    except Exception as e:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'读取图片失败: {e}', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    if len(contents) > MAX_IMAGE_SIZE:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'图片过大 ({len(contents)//1024//1024}MB)，最大支持 {MAX_IMAGE_SIZE//1024//1024}MB', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    import base64
    from langchain_core.messages import HumanMessage
    from app.memory import save_context, load_history
    from app.agents.router import _extract_text, _format_history

    b64 = base64.b64encode(contents).decode("ascii")
    data_url = f"data:{_image_mime(ext)};base64,{b64}"

    username = current_user.username
    try:
        history = load_history(conversation_id)
    except Exception:
        history = []

    history_block = _format_history(history) if history else ""
    user_prompt = q
    if history_block:
        user_prompt = f"对话历史：\n{history_block}\n\n问题：{q}"

    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": user_prompt},
    ])

    try:
        vision_llm = get_vision_llm()
    except Exception as e:
        async def err_gen():
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'视觉模型初始化失败: {e}', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    logger.info(f"[trace_id={trace_id}] 收到图片问答: file={file.filename}, question={q[:50]}, model={getattr(vision_llm, 'model_name', '')}")

    if conversation_id and not conversation_id.startswith("temp-"):
        try:
            meta = get_conversation(conversation_id)
            if not meta or not meta.get("title") or meta.get("title") == "新对话":
                title = f"[图片问答] {q.strip()[:25]}"
                upsert_conversation(conversation_id, username=username, title=title, bump_turn=False)
        except Exception:
            pass

    async def generate():
        try:
            yield f"data: {json.dumps({'type': 'intent', 'content': {'intent': 'image_qa', 'confidence': 1.0}, 'trace_id': trace_id})}\n\n"
            yield f"data: {json.dumps({'type': 'sources', 'content': [], 'trace_id': trace_id})}\n\n"

            full = ""
            try:
                for chunk in vision_llm.stream([msg]):
                    text = _extract_text(chunk)
                    if not text:
                        continue
                    full += text
                    yield f"data: {json.dumps({'type': 'chunk', 'content': text, 'trace_id': trace_id})}\n\n"
            except Exception as e:
                logger.warning(f"[trace_id={trace_id}] vision stream failed, fallback to invoke: {e}")
                try:
                    ans = vision_llm.invoke([msg])
                    text = _extract_text(ans)
                    if text:
                        full = text
                        yield f"data: {json.dumps({'type': 'chunk', 'content': text, 'trace_id': trace_id})}\n\n"
                except Exception as fe:
                    logger.error(f"[trace_id={trace_id}] vision invoke fallback failed: {fe}")
                    full = f"（图片识别失败: {fe}）"
                    yield f"data: {json.dumps({'type': 'chunk', 'content': full, 'trace_id': trace_id})}\n\n"

            try:
                save_context(conversation_id, f"[图片] {file.filename}\n{q}", full)
            except Exception as e:
                logger.warning(f"[trace_id={trace_id}] save_context failed: {e}")

            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': full, 'sources': [], 'data_source': 'vision', 'intent': 'image_qa', 'intent_confidence': 1.0, 'detected_language': 'auto', 'trace_id': trace_id}})}\n\n"

        except asyncio.CancelledError:
            logger.info(f"[trace_id={trace_id}] 图片问答流式请求被客户端取消")
        except Exception as e:
            logger.error(f"[trace_id={trace_id}] 图片问答失败: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'final', 'content': {'answer': f'系统错误: {e}', 'sources': [], 'data_source': 'error', 'trace_id': trace_id, 'intent': 'image_qa_error'}})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
#  Document management
# ---------------------------------------------------------------------------

@app.post("/upload")
@limiter.limit("10/minute")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    _validate_file(file)
    try:
        contents = await file.read()
        if len(contents) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
        file_path = _save_upload(file, contents)
        docs = load_single_document(file_path)
        if not docs:
            raise HTTPException(status_code=400, detail="无法解析该文件内容，请检查文件格式")
        add_documents_to_store(docs)
        chunks = split_documents(docs)
        logger.info(f"[trace_id={trace_id}] 文件上传成功: {file.filename}, 解析了 {len(docs)} 页/段, 分块 {len(chunks)}")
        return {"success": True, "file": os.path.basename(file_path), "chunks_added": len(chunks), "trace_id": trace_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] 文件上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"文件处理失败: {e}")


@app.post("/upload-multiple")
@limiter.limit("10/minute")
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    results = []
    for file in files:
        try:
            _validate_file(file)
            contents = await file.read()
            if len(contents) > MAX_UPLOAD_SIZE:
                results.append({"file": file.filename, "success": False, "error": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"})
                continue
            file_path = _save_upload(file, contents)
            docs = load_single_document(file_path)
            if not docs:
                results.append({"file": file.filename, "success": False, "error": "无法解析该文件内容"})
                continue
            add_documents_to_store(docs)
            chunks = split_documents(docs)
            results.append({"file": os.path.basename(file_path), "success": True, "chunks_added": len(chunks)})
        except Exception as e:
            results.append({"file": file.filename, "success": False, "error": str(e)})
    logger.info(f"[trace_id={trace_id}] 批量上传完成: {len(files)} 文件, {sum(1 for r in results if r['success'])} 成功")
    return {"results": results, "trace_id": trace_id}


@app.get("/documents")
async def list_documents(current_user: User = Depends(get_current_active_user)):
    try:
        chain = _get_chain()
        docs = chain.list_documents()
        return {"documents": docs}
    except Exception as e:
        logger.error(f"获取文档列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取文档列表失败: {e}")


@app.delete("/documents/{file_name}")
async def delete_document(
    file_name: str,
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4())[:8])
    if ".." in file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="文件名包含非法字符")
    try:
        # Remove from vectorstore
        remove_document_from_store(file_name)
        # Remove from disk
        file_path = os.path.join(config.DATA_PATH, file_name)
        if os.path.isfile(file_path):
            os.remove(file_path)
        logger.info(f"[trace_id={trace_id}] 删除文档成功: {file_name}")
        return {"success": True, "file": file_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] 删除文档失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除文档失败: {e}")


@app.post("/rebuild-vectorstore")
async def rebuild_vectorstore_endpoint(current_user: User = Depends(get_current_active_user)):
    try:
        rebuild_vectorstore()
        logger.info("向量库重建完成")
        return {"success": True, "message": "向量库重建完成"}
    except Exception as e:
        logger.error(f"向量库重建失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"向量库重建失败: {e}")


# ---------------------------------------------------------------------------
#  Conversation management
# ---------------------------------------------------------------------------

@app.get("/conversations")
async def list_conversations_endpoint(
    current_user: User = Depends(get_current_active_user),
    search: Optional[str] = Query(None, description="搜索关键词"),
):
    try:
        conversations = list_conversations(username=current_user.username)
        if search:
            search_lower = search.lower()
            conversations = [
                c for c in conversations
                if search_lower in c.get("title", "").lower() or search_lower in c.get("preview", "").lower()
            ]
        return {"conversations": conversations}
    except Exception as e:
        logger.error(f"获取对话列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取对话列表失败: {e}")


@app.get("/conversations/{conversation_id}")
async def get_conversation_endpoint(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        conv = get_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"conversation": conv}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取对话详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取对话详情失败: {e}")


@app.put("/conversations/{conversation_id}")
async def update_conversation_endpoint(
    conversation_id: str,
    update: ConversationUpdate,
    current_user: User = Depends(get_current_active_user),
):
    try:
        updated = upsert_conversation(
            conversation_id,
            username=current_user.username,
            title=update.title,
        )
        return {"conversation": updated}
    except Exception as e:
        logger.error(f"更新对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新对话失败: {e}")


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        deleted = delete_conversation(conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="对话不存在")
        logger.info(f"删除对话成功: {conversation_id}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除对话失败: {e}")


@app.post("/conversations/batch-delete")
async def batch_delete_conversations(
    req: BatchDeleteRequest,
    current_user: User = Depends(get_current_active_user),
):
    try:
        deleted_count = 0
        for conv_id in req.conversation_ids:
            if delete_conversation(conv_id):
                deleted_count += 1
        logger.info(f"批量删除完成: 删除了 {deleted_count}/{len(req.conversation_ids)} 个对话")
        return {"success": True, "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"批量删除对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量删除对话失败: {e}")


@app.post("/conversations/{conversation_id}/export")
async def export_conversation_endpoint(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        conv = get_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="对话不存在")
        return export_conversation(conversation_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导出对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出对话失败: {e}")


@app.post("/conversations/{conversation_id}/clear")
async def clear_conversation_endpoint(
    conversation_id: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        clear_memory(conversation_id)
        logger.info(f"清除对话记忆成功: {conversation_id}")
        return {"success": True}
    except Exception as e:
        logger.error(f"清除对话记忆失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清除对话记忆失败: {e}")


# ---------------------------------------------------------------------------
#  User profile
# ---------------------------------------------------------------------------

@app.get("/profile")
async def get_profile_endpoint(current_user: User = Depends(get_current_active_user)):
    return get_profile(current_user.username)


@app.put("/profile")
async def update_profile_endpoint(
    update: UserProfileUpdate,
    current_user: User = Depends(get_current_active_user),
):
    updated = update_profile(current_user.username, update.dict(exclude_unset=True))
    return {"profile": updated}


# ---------------------------------------------------------------------------
#  Long text tools
# ---------------------------------------------------------------------------

@app.post("/long-text")
@limiter.limit("10/minute")
async def long_text_tool(
    req: LongTextRequest,
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    try:
        if req.operation == "summary":
            result = long_text_summarize(req.text, max_chars=req.max_chars)
        elif req.operation == "translate":
            result = long_text_translate(req.text, target_language=req.target_language or "zh")
        elif req.operation == "qa":
            if not req.question:
                raise HTTPException(status_code=400, detail="问答模式需要提供 question 参数")
            result = answer_against_text(req.text, req.question)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的操作类型: {req.operation}")
        return {"result": result, "operation": req.operation}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"长文本工具处理失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"长文本处理失败: {e}")


# ---------------------------------------------------------------------------
#  Memory / Learning
# ---------------------------------------------------------------------------

@app.post("/remember-turn")
async def remember_turn_endpoint(
    text: str,
    current_user: User = Depends(get_current_active_user),
):
    try:
        profile = remember_turn(current_user.username, text)
        return {"profile": profile}
    except Exception as e:
        logger.error(f"记忆学习失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"记忆学习失败: {e}")


# ---------------------------------------------------------------------------
#  Feedback
# ---------------------------------------------------------------------------

@app.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    current_user: User = Depends(get_current_active_user),
):
    try:
        feedback_dir = os.path.join(config.DATA_PATH, "feedback")
        os.makedirs(feedback_dir, exist_ok=True)
        feedback_data = {
            "message_id": req.message_id,
            "conversation_id": req.conversation_id,
            "username": current_user.username,
            "rating": req.rating,
            "comment": req.comment,
            "timestamp": datetime.utcnow().isoformat(),
        }
        feedback_path = os.path.join(feedback_dir, f"{req.message_id}_{int(_time.time())}.json")
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(feedback_data, f, ensure_ascii=False, indent=2)
        logger.info(f"反馈已保存: message_id={req.message_id}, rating={req.rating}")
        return {"success": True}
    except Exception as e:
        logger.error(f"保存反馈失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存反馈失败: {e}")


# ---------------------------------------------------------------------------
#  Example questions
# ---------------------------------------------------------------------------

@app.get("/example-questions")
async def get_example_questions(
    current_user: User = Depends(get_current_active_user),
    limit: int = Query(5, ge=1, le=10),
):
    try:
        chain = _get_chain()
        docs = chain.list_documents()
        if not docs:
            return {"questions": [
                "介绍一下项目的主要功能",
                "如何上传和管理文档？",
                "支持哪些文件格式？",
                "问答系统是如何工作的？",
                "如何使用联网搜索功能？",
            ]}

        questions = []
        keywords = set()
        for doc in docs[:10]:
            doc_name = doc.get("file", "")
            if doc_name:
                questions.append(f"介绍一下 {doc_name} 的内容")
                questions.append(f"{doc_name} 中有哪些重要信息？")

        return {"questions": list(set(questions))[:limit]}
    except Exception as e:
        logger.error(f"获取示例问题失败: {e}", exc_info=True)
        return {"questions": [
            "介绍一下项目的主要功能",
            "如何上传和管理文档？",
            "支持哪些文件格式？",
            "问答系统是如何工作的？",
            "如何使用联网搜索功能？",
        ]}


# ---------------------------------------------------------------------------
#  System status
# ---------------------------------------------------------------------------

@app.get("/stats")
async def get_stats(current_user: User = Depends(get_current_active_user)):
    try:
        chain = _get_chain()
        return chain.get_stats()
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {e}")


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "本地知识库问答系统", "version": "3.0.0"}


# ---------------------------------------------------------------------------
#  Shutdown
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on application shutdown."""
    global _rag_chain
    from app.rag_chain import _executor, _TOKEN_CACHE
    from app.tools.web_search import close_client
    logger.info("正在关闭应用，清理资源...")
    if _executor:
        _executor.shutdown(wait=False)
        logger.info("线程池已关闭")
    _TOKEN_CACHE.clear()
    logger.info("缓存已清理")
    await close_client()
    logger.info("HTTP客户端已关闭")


# ---------------------------------------------------------------------------
#  Share Links (read-only public tokens)
# ---------------------------------------------------------------------------

SHARE_DIR = os.path.join(config.DATA_PATH, "shares")
SHARE_DEFAULT_TTL_HOURS = 24


def _ensure_share_dir() -> None:
    os.makedirs(SHARE_DIR, exist_ok=True)


def _purge_expired_shares() -> None:
    try:
        if not os.path.isdir(SHARE_DIR):
            return
        now = _time.time()
        for fn in os.listdir(SHARE_DIR):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(SHARE_DIR, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if float(meta.get("expires_at", 0)) < now:
                    os.remove(p)
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"清理过期分享失败: {e}")


class ShareCreateRequest(BaseModel):
    ttl_hours: Optional[int] = 24
    messages: Optional[List[Dict[str, Any]]] = None


@app.post("/conversations/{conversation_id}/share")
async def create_share_link(
    conversation_id: str,
    req: ShareCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """为对话生成一个只读的临时分享 token。"""
    trace_id = str(uuid.uuid4())[:8]
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    payload = export_conversation(conversation_id)
    # 如果前端提供了完整消息数组，优先使用
    if req.messages:
        payload["history"] = req.messages
    _ensure_share_dir()
    _purge_expired_shares()
    token = secrets.token_urlsafe(24)
    ttl = max(1, min(int(req.ttl_hours or SHARE_DEFAULT_TTL_HOURS), 24 * 14))
    expires_at = _time.time() + ttl * 3600
    share_record: Dict[str, Any] = {
        "token": token,
        "conversation_id": conversation_id,
        "owner": current_user.username,
        "title": conv.get("title") or "对话",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at,
        "data": payload,
    }
    with open(os.path.join(SHARE_DIR, f"{token}.json"), "w", encoding="utf-8") as f:
        json.dump(share_record, f, ensure_ascii=False, indent=2)
    logger.info(f"[trace_id={trace_id}] 创建分享链接: conv={conversation_id} token={token[:8]}... ttl={ttl}h")
    return {
        "success": True,
        "token": token,
        "url_path": f"/shared/{token}",
        "expires_at": expires_at,
        "ttl_hours": ttl,
    }


@app.get("/share/{token}")
async def get_shared_conversation(token: str):
    """公开访问：根据 token 获取只读对话内容（无需鉴权）。"""
    if not token or len(token) < 8 or len(token) > 128:
        raise HTTPException(status_code=400, detail="无效的分享令牌")
    path = os.path.join(SHARE_DIR, f"{token}.json")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as e:
        logger.error(f"读取分享失败: {e}")
        raise HTTPException(status_code=500, detail="分享内容读取失败")
    if float(rec.get("expires_at", 0)) < _time.time():
        try:
            os.remove(path)
        except Exception:
            pass
        raise HTTPException(status_code=410, detail="分享链接已过期")
    return {
        "title": rec.get("title", "对话"),
        "conversation_id": rec.get("conversation_id"),
        "created_at": rec.get("created_at"),
        "expires_at": rec.get("expires_at") * 1000 if rec.get("expires_at") else None,
        "data": rec.get("data", {}),
    }
