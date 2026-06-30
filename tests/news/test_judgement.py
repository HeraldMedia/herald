from herald.validator.news.real_news import is_paid
from herald.validator.news.topic_match import topic_matched


def test_paid_by_url_path():
    assert is_paid("https://nytimes.com/press-release/x", "Body")[0] is True
    assert is_paid("https://nytimes.com/sponsored/x", "Body")[0] is True


def test_paid_by_disclosure_label():
    assert is_paid("https://nytimes.com/world/x", "This is Sponsored Content from Acme")[0] is True
    assert is_paid("https://nytimes.com/world/x", "In partnership with Acme Corp")[0] is True


def test_editorial_not_paid():
    paid, reason = is_paid("https://nytimes.com/2026/01/01/world/x", "A normal news report.")
    assert paid is False and reason == ""


def test_topic_match_requires_keyword():
    brief = {"id": "b1", "keywords": ["bittensor", "subnet"]}
    assert topic_matched("A story about the Bittensor network.", brief) is True
    assert topic_matched("An unrelated cooking article.", brief) is False


def test_topic_match_no_keywords_passes():
    assert topic_matched("anything", {"id": "b1"}) is True


# ── LLM fallback tier ─────────────────────────────────────────────────────────

def test_llm_flags_paid_when_rules_miss():
    # rules don't flag, but the LLM says paid
    judge = lambda q, t: True
    paid, reason = is_paid("https://nytimes.com/world/x", "Subtle native ad text.", judge_fn=judge)
    assert paid is True and reason == "llm"


def test_llm_not_called_result_ignored_when_rules_already_pass():
    judge = lambda q, t: False
    assert is_paid("https://nytimes.com/world/x", "Normal report.", judge_fn=judge) == (False, "")


def test_llm_decides_topic_when_keywords_absent():
    brief = {"id": "b1", "keywords": ["bittensor"], "topic": "Bittensor"}
    assert topic_matched("A story with no obvious keyword.", brief, judge_fn=lambda q, t: True) is True
    assert topic_matched("A story with no obvious keyword.", brief, judge_fn=lambda q, t: False) is False


def test_llm_unsure_falls_back_to_rules():
    brief = {"id": "b1", "keywords": ["bittensor"]}
    # judge returns None (unsure) -> rules say no (keyword absent)
    assert topic_matched("unrelated", brief, judge_fn=lambda q, t: None) is False
