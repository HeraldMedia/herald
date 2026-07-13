from herald.validator.news.real_news import is_paid
from herald.validator.news.registry import Outlet


def test_generic_sponsored_content_path_now_caught():
    # regression: /sponsored-content/ (a hyphenated segment) was missed before
    assert is_paid("https://edition.cnn.com/sponsored-content/acme", "", None)[0] is True
    assert is_paid("https://x.com/brand-studio/acme/story", "", None)[0] is True


def test_per_outlet_paid_pattern_rejects_branded_url():
    # Forbes: the BrandVoice contributor program is paid; editorial /sites/staff/ is not.
    forbes = Outlet(
        outlet_id="forbes", tier=1, domains=["forbes.com", "www.forbes.com"],
        paid_patterns=[r"brandvoice", r"/councils?/"],
        paid_markers=["BrandVoice", "Council Post"],
    )
    paid, reason = is_paid(
        "https://www.forbes.com/sites/acmecorpbrandvoice/2026/01/05/acme/", "", None, outlet=forbes)
    assert paid is True and reason == "outlet_paid_pattern"
    # a real editorial article on the same /sites/ prefix must still pass
    assert is_paid("https://www.forbes.com/sites/johnstaffwriter/2026/01/05/markets/",
                   "editorial body", None, outlet=forbes)[0] is False


def test_per_outlet_paid_marker_in_text():
    bi = Outlet(outlet_id="bi", tier=1, domains=["businessinsider.com"],
                paid_markers=["Insider Studios"])
    paid, reason = is_paid("https://businessinsider.com/acme-story",
                           "Acme ... This article was created by Insider Studios.", None, outlet=bi)
    assert paid is True and reason == "outlet_paid_marker"


def test_no_outlet_falls_back_to_generic():
    # outlet=None keeps the original global behavior
    assert is_paid("https://x.com/press-release/acme", "", None)[0] is True
    assert is_paid("https://x.com/world/real-story", "editorial", None)[0] is False
