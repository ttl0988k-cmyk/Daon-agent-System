# -*- coding: utf-8 -*-
"""
api.memory_embed - DAON 로컬 다국어 임베딩 엔진 (Ruflo 로직 포팅)
============================================================
- 384차원 float32 벡터 (1,536바이트 BLOB) 생성
- 로컬 sentence-transformers / onnxruntime 기반 (API 비용 0원, 외부 네트워크 불필요)
- 다국어 (한국어 + 영어) 최적화
"""
from __future__ import annotations

import logging
import threading
from typing import Optional, Union
import numpy as np

_log = logging.getLogger("api.memory_embed")

_MODEL = None
_MODEL_LOCK = threading.Lock()
_DIM = 384


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer
            # 로컬 캐시에 기설치된 intfloat/multilingual-e5-small (384차원 고품질 다국어)
            # 없으면 paraphrase-multilingual-MiniLM-L12-v2 순으로 폴백
            for m_name in ("intfloat/multilingual-e5-small", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
                try:
                    _log.info("[MemoryEmbed] 로컬 임베딩 모델 로딩 시도: %s", m_name)
                    _MODEL = SentenceTransformer(m_name)
                    _log.info("[MemoryEmbed] 모델 로드 성공: %s (차원=%d)", m_name, _DIM)
                    break
                except Exception as ex:
                    _log.warning("[MemoryEmbed] 모델 %s 로드 실패: %s", m_name, ex)
            return _MODEL
        except Exception as e:
            _log.error("[MemoryEmbed] sentence_transformers 로드 실패: %s", e)
            return None


def embed_text(text: str, is_query: bool = False) -> bytes:
    """텍스트를 384차원 float32 BLOB 바이트(1536 bytes)로 임베딩한다.
    
    e5 모델 관례에 따라 쿼리는 'query: ', 문서는 'passage: ' 접두어를 자동 부여해
    한국어/영어 의미 검색 품질을 극대화한다.
    """
    if not text or not text.strip():
        return np.zeros(_DIM, dtype=np.float32).tobytes()

    text_clean = text.strip()
    prefix = "query: " if is_query else "passage: "
    input_text = prefix + text_clean

    model = _get_model()
    if model is None:
        return np.zeros(_DIM, dtype=np.float32).tobytes()

    try:
        vec = model.encode(input_text, normalize_embeddings=True)
        vec_f32 = np.asarray(vec, dtype=np.float32)
        if vec_f32.shape[0] != _DIM:
            # 384차원 패딩/자르기 방어
            padded = np.zeros(_DIM, dtype=np.float32)
            sz = min(_DIM, vec_f32.shape[0])
            padded[:sz] = vec_f32[:sz]
            vec_f32 = padded
        return vec_f32.tobytes()
    except Exception as e:
        _log.warning("[MemoryEmbed] embed_text 실패: %s", e)
        return np.zeros(_DIM, dtype=np.float32).tobytes()


def embed_batch(texts: list[str], is_query: bool = False) -> list[bytes]:
    """텍스트 목록을 일괄 임베딩하여 bytes 리스트로 반환한다 (배치 가속)."""
    if not texts:
        return []

    prefix = "query: " if is_query else "passage: "
    inputs = [prefix + (t.strip() if t and t.strip() else "") for t in texts]

    model = _get_model()
    if model is None:
        zero_bytes = np.zeros(_DIM, dtype=np.float32).tobytes()
        return [zero_bytes] * len(texts)

    try:
        vecs = model.encode(inputs, normalize_embeddings=True, batch_size=32)
        results = []
        for v in vecs:
            vf = np.asarray(v, dtype=np.float32)
            if vf.shape[0] != _DIM:
                padded = np.zeros(_DIM, dtype=np.float32)
                sz = min(_DIM, vf.shape[0])
                padded[:sz] = vf[:sz]
                vf = padded
            results.append(vf.tobytes())
        return results
    except Exception as e:
        _log.warning("[MemoryEmbed] embed_batch 실패: %s", e)
        zero_bytes = np.zeros(_DIM, dtype=np.float32).tobytes()
        return [zero_bytes] * len(texts)


def vector_from_blob(blob: Optional[bytes]) -> Optional[np.ndarray]:
    """SQLite BLOB에서 numpy float32 벡터로 복원."""
    if not blob or len(blob) < _DIM * 4:
        return None
    try:
        arr = np.frombuffer(blob[: _DIM * 4], dtype=np.float32)
        # 내적 정규화 보장
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr
    except Exception:
        return None


def cosine(a: Union[bytes, np.ndarray], b: Union[bytes, np.ndarray]) -> float:
    """두 벡터 간의 코사인 유사도 계산 (-1.0 ~ 1.0). 정규화 벡터의 경우 단순 내적."""
    va = a if isinstance(a, np.ndarray) else vector_from_blob(a)
    vb = b if isinstance(b, np.ndarray) else vector_from_blob(b)

    if va is None or vb is None:
        return 0.0

    try:
        dot = float(np.dot(va, vb))
        return max(-1.0, min(1.0, dot))
    except Exception:
        return 0.0

