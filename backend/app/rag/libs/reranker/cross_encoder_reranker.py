"""
Cross-Encoder Reranker for precision relevance scoring.

Implements the reranker interface using sentence-transformers CrossEncoder
models. Designed for the v3.0 Parent-Child RAG pipeline.

Default model: BAAI/bge-reranker-v2-m3
  - Lightweight (560MB), Chinese-optimized
  - 8192 token context window (can score long parent chunks)
  - Good balance of speed vs accuracy for local deployment

Usage in RAG pipeline:
  1. Retrieve Top 15 child chunks → resolve to unique parent chunks
  2. Cross-Encoder scores each parent chunk against the original query
  3. Keep Top 3-5 parent chunks as LLM context
"""

from typing import List, Optional, Any, Dict


class RerankerBackend:
    """Abstract reranker backend interface."""

    def rerank(self, query: str, results: list, top_k: int) -> list:
        raise NotImplementedError


class NoOpReranker(RerankerBackend):
    """Identity reranker (pass-through)."""
    def rerank(self, query: str, results: list, top_k: int) -> list:
        return results[:top_k]


class CrossEncoderReranker(RerankerBackend):
    """
    Cross-encoder based reranker using sentence-transformers.

    Scores each document-query pair with precision relevance using
    a cross-encoder model (e.g., BAAI/bge-reranker-v2-m3).

    The reranker receives parent chunks (resolved from child chunks)
    and returns them sorted by cross-encoder relevance score.
    """

    def __init__(self, model_name: str = "/app/models_cache/bge-reranker-v2-m3"):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(
                    self.model_name,
                    max_length=8192,  # Support long parent chunks
                    local_files_only=True,  # 禁止联网检查版本，从本地加载
                )
            except ImportError:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Rerank results by cross-encoder relevance score.

        Args:
            query: Original user query.
            results: List of dicts, each with at least a "content" key
                     (the parent chunk text).
            top_k: Number of top results to return after reranking.

        Returns:
            Reranked list of results, sorted by cross-encoder score descending.
            Each result dict receives a "rerank_score" key.
        """
        if not results:
            return results

        self._load_model()

        # Prepare (query, document) pairs
        texts = [r.get("content", r.get("text", "")) for r in results]
        pairs = [[query, text] for text in texts if text]

        if not pairs:
            return results[:top_k]

        # Get cross-encoder scores
        try:
            scores = self._model.predict(pairs)
        except Exception:
            # If model fails (e.g., OOM), fall back to original order
            for r in results:
                r["rerank_score"] = 0.0
            return results[:top_k]

        # Attach scores to results (only for those that had content)
        scored_idx = 0
        for r in results:
            text = r.get("content", r.get("text", ""))
            if text:
                if scored_idx < len(scores):
                    r["rerank_score"] = float(scores[scored_idx])
                    scored_idx += 1
                else:
                    r["rerank_score"] = 0.0
            else:
                r["rerank_score"] = 0.0

        # Sort by rerank score descending
        results.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)

        return results[:top_k]