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
