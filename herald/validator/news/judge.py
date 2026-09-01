"""Consensus-safe LLM judge for the ambiguous judgement checks.

Pinned model, temperature 0, discrete yes/no — used only as a fallback after the
deterministic rules, and only when an LLM provider is configured.
"""

import re

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


def _verdict(content: str):
    """Read a yes/no answer, or None when the model did not give one.

    An unparsed answer is not neutral: topic_matched() falls back to accepting the article when a
    brief has no keywords, so a stray "**Yes**" would silently pass everything. Strip the wrappers
    a model may add (markdown, quotes, punctuation) before deciding it said nothing.
    """
    first = re.sub(r"[^a-z]", " ", (content or "").strip().lower()).split()
    if not first:
        bt.logging.warning("LLM judge returned an empty answer; treating it as unavailable")
        return None
    if first[0] == "yes":
        return True
    if first[0] == "no":
        return False
    bt.logging.warning(
        "LLM judge answered %r, which is neither yes nor no; treating it as unavailable",
        (content or "")[:80],
    )
    return None


def judge(question: str, text: str):
    """Return True/False, or None when the model is unavailable or unsure."""
    try:
        client = _get_client()
        # Prefer the explicitly-pinned model so every validator uses the SAME model
        # (a per-provider default would diverge across validators).
        model = HERALD_REF_MODEL_ID or getattr(client, "BRIEF_EVALUATION_MODEL", None)
        prompt = f"{question}\nAnswer 'yes' or 'no' only.\n\nArticle text:\n{(text or '')[:8000]}"
        resp = client._make_request(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return _verdict(resp["choices"][0]["message"]["content"])
    except Exception as e:
        bt.logging.warning(f"LLM judge failed: {e}")
        return None
