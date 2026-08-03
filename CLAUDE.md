# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

本地知识库问答系统 (Local Knowledge Base Q&A System) — a RAG-based document Q&A platform with a Python/FastAPI backend and Vue 3 frontend. Supports uploading documents, hybrid retrieval (BM25 + vector search + RRF fusion), streaming SSE responses, and multi-turn conversation memory.

## Commands

### Backend (Python 3.10+)

```bash
cd backend

# Create and activate venv
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend (Node.js >= 22)

```bash
cd frontend
npm install
npm run dev        # Dev server on http://localhost:5173
npm run build      # Production build
npm run lint       # Run all linters (oxlint + eslint)
npm run format     # Prettier format
npm run test:unit  # Vitest unit tests
npm run test:e2e   # Playwright E2E tests
```

### Docker

```bash
docker-compose up -d     # Start all services
docker-compose down      # Stop all services
```

## Architecture

### Backend (`backend/app/`)

3 core files:

- **`config.py`** — All configuration. Switch `MODEL_BACKEND` (`local`/`ollama`/`openai`/`baidu_qianfan`/`modelscope`) to change LLM and embedding providers. Contains chunk sizing, retriever count, and API keys. Default is `modelscope` with TF-IDF embeddings.

- **`rag_chain.py`** — The entire RAG pipeline:
  - `get_rag_chain()` / `get_streaming_rag_chain()` — Factory functions that return callable chains. Each call creates a closure capturing the vectorstore, LLM, and prompt. The chain accepts `{"question": str, "conversation_id": str}` dicts.
  - `hybrid_search()` — BM25 (keyword) + Chroma vector similarity search, fused via `rrf_fusion()` (Reciprocal Rank Fusion).
  - `get_vectorstore()` — Singleton Chroma vectorstore. Loads from `chroma_db/` if exists, otherwise loads documents from `data/`, splits them with `RecursiveCharacterTextSplitter`, embeds, and persists.
  - `get_memory()` — Per-conversation `ConversationBufferWindowMemory` (k=5 window), keyed by `conversation_id`.
  - `BM25Retriever` — Singleton BM25 index using `rank_bm25`. Rebuilt on every vectorstore rebuild.
  - `SimpleTFIDFEmbeddings` — Custom TF-IDF embedding (768-dim) used when `MODEL_BACKEND` is `local` or `modelscope`. Builds vocabulary from document corpus.
  - Custom loaders: `DocxLoader`, `PptxLoader`, `ImageOCRLoader` (pytesseract + PIL).
  - `rebuild_vectorstore()` — Deletes `chroma_db/` directory and rebuilds from scratch. Called on every file upload/delete.
  - Fallback behavior: When no documents found or relevance overlap < 0.2, the LLM answers directly without context (data_source = "llm").

- **`main.py`** — FastAPI application:
  - JWT auth via `python-jose` + `bcrypt`. Hardcoded user `admin/admin123`. Token endpoint at `/token`.
  - Rate limiting via `slowapi` (50/min for `/ask`, 30/min for `/ask/stream`, 10/min for uploads).
  - SSE streaming at `/ask/stream` — returns `type: "chunk"|"sources"|"final"` events.
  - File upload (`/upload`, `/upload-multiple`) triggers full vectorstore rebuild.
  - Document CRUD at `/documents` and `/document/{filename}`.
  - Startup event initializes the RAG chain and vectorstore globally.

### Frontend (`frontend/src/`)

Single-page app. All logic is in `App.vue` (~1500 lines, single-file component with template + script + styles). No separate route pages — the router exists but has no routes defined. Pinia store (`stores/counter.js`) is scaffolded but unused.

Key behaviors:
- Login screen → JWT token stored in localStorage → main chat interface.
- SSE streaming via `fetch` + `ReadableStream` reader (not EventSource, to support POST with auth headers).
- Chat history persisted in localStorage (`chatData` key): conversation list + messages per conversation, survives page refresh.
- Temporary chats (`temp-*` ID) become formal chats (`conv-*` ID) on first message send.
- Vite dev server proxies `/api` → `http://localhost:8000` (strips `/api` prefix).

### Data Flow

```
User uploads document → POST /upload → file saved to backend/data/
  → rebuild_vectorstore() → Chroma + BM25 index rebuilt
User asks question → POST /ask/stream → hybrid_search()
  → BM25 + vector → RRF fusion → top-k docs → prompt template
  → LLM streaming → SSE chunks → frontend renders typing effect
```

### Key Configuration Points

- `CHUNK_SIZE=500`, `CHUNK_OVERLAP=50`, `RETRIEVER_K=3` in `config.py`
- `ConversationBufferWindowMemory(k=5)` — keeps last 5 exchanges per conversation
- RRF fusion uses `k=60` constant, returns top 5 (hardcoded in `rrf_fusion` call)
- Token overlap threshold for relevance check: 0.2 (in `rag_chain_with_sources`)
- `requirements.txt` only lists `zstandard==0.25.0` — actual dependencies are in the venv but not pinned
- Default credentials: `admin` / `admin123`
- `MODELSCOPE_API_KEY` is hardcoded in both `config.py` and `docker-compose.yml` — should be moved to env vars in production