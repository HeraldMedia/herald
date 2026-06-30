"""Consensus-safe LLM judge for the ambiguous judgement checks.

Pinned model, temperature 0, discrete yes/no — used only as a fallback after the
deterministic rules, and only when an LLM provider is configured.
"""

import bittensor as bt

from herald.validator.utils.config import (
    CHUTES_API_KEY,
    HERALD_REF_MODEL_ID,
    OPENROUTER_API_KEY,
)

PAID_QUESTION = "Is this article paid or sponsored content rather than independent editorial reporting?"


def topic_question(brief: dict) -> str:
    topic = brief.get("topic") or brief.get("title") or ""
    return f"Is this news article about the following topic: {topic}?"


def llm_available() -> bool:
    return bool(CHUTES_API_KEY or OPENROUTER_API_KEY)


def _get_client():
    from herald.validator.clients.llm_client import get_llm_client
    return get_llm_client()


def judge(question: str, text: str):
    """Return True/False, or None when the model is unavailable or unsure."""
    try:
        client = _get_client()
        model = getattr(client, "BRIEF_EVALUATION_MODEL", None) or HERALD_REF_MODEL_ID
        prompt = f"{question}\nAnswer 'yes' or 'no' only.\n\nArticle text:\n{(text or '')[:8000]}"
        resp = client._make_request(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = resp["choices"][0]["message"]["content"].strip().lower()
        if content.startswith("yes"):
            return True
        if content.startswith("no"):
            return False
        return None
    except Exception as e:
        bt.logging.warning(f"LLM judge failed: {e}")
        return None
