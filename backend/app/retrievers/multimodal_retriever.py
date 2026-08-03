"""
多模态检索 — 基于 CLIP 的图文混合检索。

当前系统对图片仅做 OCR 提取文字，未建图像向量。本模块:
  1. CLIPImageEmbedder: 用 CLIP 模型对图片和文本统一编码到同一向量空间。
  2. MultimodalVectorStore: 独立的 Chroma collection 存储图文向量。
  3. 支持"以文搜图"和"以图搜图"。

接入点:
  - 文档上传时: 若为图片，调用 add_image 同时存入多模态库
  - 检索时: 可将多模态结果与文本 RAG 结果融合

依赖: pip install torch transformers pillow
"""

import logging
import os
from typing import List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

CLIP_TEXT_PROMPT = "a photo of {}"
CLIP_IMAGE_DIM = 512


class CLIPImageEmbedder:
    """CLIP 图文统一嵌入器。"""

    def __init__(self, model_name: str = "openai/clip-vit-base-patch32"):
        self.model_name = model_name
        self._model = None
        self._processor = None
        self._device = "cpu"

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = CLIPModel.from_pretrained(self.model_name).to(self._device)
            self._processor = CLIPProcessor.from_pretrained(self.model_name)
            self._model.eval()
            logger.info(f"CLIP 模型加载完成: {self.model_name} on {self._device}")
        except ImportError:
            logger.warning("transformers/torch 未安装，多模态检索不可用。请: pip install torch transformers pillow")
            raise

    def embed_text(self, text: str) -> List[float]:
        """将文本编码为 CLIP 向量。"""
        self._load()
        import torch
        inputs = self._processor(text=[text], return_tensors="pt", padding=True, truncation=True).to(self._device)
        with torch.no_grad():
            features = self._model.get_text_features(**inputs)
        vec = features[0].cpu().tolist()
        return self._normalize(vec)

    def embed_image(self, image_path: str) -> List[float]:
        """将图片编码为 CLIP 向量。"""
        self._load()
        from PIL import Image
        import torch
        image = Image.open(image_path).convert("RGB")
        inputs = self._processor(images=[image], return_tensors="pt").to(self._device)
        with torch.no_grad():
            features = self._model.get_image_features(**inputs)
        vec = features[0].cpu().tolist()
        return self._normalize(vec)

    @staticmethod
    def _normalize(vec: List[float]) -> List[float]:
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec] if norm > 0 else vec

    def embed_query(self, text: str) -> List[float]:
        return self.embed_text(text)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_text(t) for t in texts]


class MultimodalVectorStore:
    """图文混合向量库，使用独立 Chroma collection。"""

    def __init__(self, persist_dir: Optional[str] = None, collection_name: str = "multimodal"):
        from app import config
        self.persist_dir = persist_dir or os.path.join(config.BASE_DIR, "multimodal_db")
        self.collection_name = collection_name
        self._embedder: Optional[CLIPImageEmbedder] = None
        self._store = None

    def _get_embedder(self) -> CLIPImageEmbedder:
        if self._embedder is None:
            self._embedder = CLIPImageEmbedder()
        return self._embedder

    def _get_store(self):
        if self._store is not None:
            return self._store
        from langchain_chroma import Chroma
        os.makedirs(self.persist_dir, exist_ok=True)
        self._store = Chroma(
            collection_name=self.collection_name,
            embedding_function=self._get_embedder(),
            persist_directory=self.persist_dir,
        )
        return self._store

    def add_image(self, image_path: str, metadata: Optional[dict] = None) -> bool:
        """将图片加入多模态库。"""
        try:
            embedder = self._get_embedder()
            vector = embedder.embed_image(image_path)
            doc = Document(
                page_content=f"[图片] {os.path.basename(image_path)}",
                metadata={**(metadata or {}), "source": image_path, "modality": "image"},
            )
            store = self._get_store()
            store.add_texts([doc.page_content], metadatas=[doc.metadata], embeddings=[vector])
            logger.info(f"图片已加入多模态库: {image_path}")
            return True
        except Exception as e:
            logger.error(f"图片入库失败 {image_path}: {e}")
            return False

    def add_text(self, text: str, metadata: Optional[dict] = None):
        """将文本加入多模态库（用于图文联合检索）。"""
        doc = Document(
            page_content=text,
            metadata={**(metadata or {}), "modality": "text"},
        )
        store = self._get_store()
        store.add_documents([doc])

    def search_by_text(self, query: str, k: int = 3) -> List[Tuple[Document, float]]:
        """以文搜图/搜文。"""
        store = self._get_store()
        return store.similarity_search_with_score(query, k=k)

    def search_by_image(self, image_path: str, k: int = 3) -> List[Tuple[Document, float]]:
        """以图搜图。"""
        embedder = self._get_embedder()
        query_vec = embedder.embed_image(image_path)
        store = self._get_store()
        results = store.similarity_search_by_vector(query_vec, k=k)
        scores = store.similarity_search_with_score_by_vector(query_vec, k=k)
        return scores

    def get_stats(self) -> dict:
        try:
            store = self._get_store()
            return {"multimodal_count": store._collection.count()}
        except Exception:
            return {"multimodal_count": 0}


_mm_instance: Optional[MultimodalVectorStore] = None


def get_multimodal_store() -> MultimodalVectorStore:
    global _mm_instance
    if _mm_instance is None:
        _mm_instance = MultimodalVectorStore()
    return _mm_instance
