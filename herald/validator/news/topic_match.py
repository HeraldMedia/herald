"""Check that an article is on the brief's topic (rules first, optional LLM fallback)."""


def topic_matched(text: str, brief: dict, judge_fn=None) -> bool:
    keywords = brief.get("keywords") or []
    low = (text or "").lower()
    if keywords and any(k.lower() in low for k in keywords):
        return True

    if judge_fn is not None:
        from .judge import topic_question
        verdict = judge_fn(topic_question(brief), text)
        if verdict is not None:
            return verdict

    return not keywords
