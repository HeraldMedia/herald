"""Check that an article is on the brief's topic."""


def topic_matched(text: str, brief: dict) -> bool:
    keywords = brief.get("keywords") or []
    if not keywords:
        return True
    low = (text or "").lower()
    return any(k.lower() in low for k in keywords)
