"""Reranker Factory — creates reranker instances from config.

v3.0 Refactoring:
  - Default backend changed from "none" to "cross_encoder"
  - Default model changed to "BAAI/bge-reranker-v2-m3" (lightweight Chinese-optimized cross-encoder)
  - Supports: none, cross_encoder, llm
"""

from typing import Optional

# CrossEncoderReranker now imported from its own module for clarity
from .cross_encoder_reranker import CrossEncoderReranker, NoOpReranker, RerankerBackend


class LLMReranker(RerankerBackend):
    """LLM-based reranker that uses an LLM to judge relevance.

    This is a lightweight approach that asks the LLM to score each
    result-query pair on a scale of 1-10.
    """

    def __init__(self, llm_judge_fn=None):
        """Initialize LLM Reranker.

        Args:
            llm_judge_fn: Async function(query, text) -> relevance_score (0-1).
                         If None, uses a simple keyword overlap fallback.
        """
        self.llm_judge_fn = llm_judge_fn

    def rerank(self, query: str, results: list, top_k: int) -> list:
        if not results:
            return results

        if self.llm_judge_fn:
            # Use LLM judge
            scored = []
            for r in results:
                score = self.llm_judge_fn(query, r.text)
                r.score = float(score)
                scored.append(r)
            scored.sort(key=lambda x: x.score, reverse=True)
            return scored[:top_k]

        # Fallback: keyword overlap scoring
        query_words = set(query.lower().split())
        scored = []
        for r in results:
            text_words = set(r.text.lower().split())
            overlap = len(query_words & text_words)
            score = overlap / max(len(query_words), 1)
            r.score = score
            scored.append(r)
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


def create_reranker(
    backend: str = "cross_encoder",
    model_name: Optional[str] = None,
    llm_judge_fn=None,
    **kwargs
) -> RerankerBackend:
    """Create a reranker backend from configuration.

    v3.0: Default reranker is now Cross-Encoder with BGE-Reranker-v2-m3.

    Args:
        backend: One of "none", "cross_encoder", "llm"
        model_name: Model name for cross_encoder (default: BAAI/bge-reranker-v2-m3)
        llm_judge_fn: Callable for LLM reranker
        **kwargs: Additional backend-specific args

    Returns:
        RerankerBackend instance
    """
    backend = backend.lower()

    if backend == "cross_encoder":
        # v3.0: Use local BGE-Reranker-v2-m3 (离线部署，禁止联网)
        model = model_name or "/app/models_cache/bge-reranker-v2-m3"
        return CrossEncoderReranker(model_name=model)

    elif backend == "llm":
        return LLMReranker(llm_judge_fn=llm_judge_fn)

    else:  # "none" or unknown
        return NoOpReranker()