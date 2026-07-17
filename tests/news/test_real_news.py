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


def test_short_paid_marker_is_scoped_to_disclosure_window():
    outlet = Outlet(outlet_id="x", tier=1, domains=["x.com"],
                    paid_markers=["In association with"])
    late_prose = ("Independent reporting on market conditions. " * 25
                  + "The survey was conducted in association with Econsultancy.")
    assert is_paid("https://x.com/story", late_prose, None, outlet=outlet)[0] is False
    assert is_paid("https://x.com/story", "In association with Acme. Report body.",
                   None, outlet=outlet)[0] is True


def test_long_paid_disclaimer_is_checked_anywhere_in_article():
    marker = "The body of the text has not been edited in any way by our newsroom"
    outlet = Outlet(outlet_id="x", tier=1, domains=["x.com"], paid_markers=[marker])
    text = "Independent-looking copy. " * 100 + marker
    assert is_paid("https://x.com/story", text, None, outlet=outlet)[0] is True


def test_generic_paid_phrase_in_reported_prose_is_not_a_disclosure():
    reported = ("A third of brands admit to deliberately not disclosing influencer "
                "marketing as sponsored content as they believe doing so affects trust.")
    assert is_paid("https://x.com/story", reported, None)[0] is False
    assert is_paid("https://x.com/story", "Sponsored Content from Acme", None)[0] is True


def test_no_outlet_falls_back_to_generic():
    # outlet=None keeps the original global behavior
    assert is_paid("https://x.com/press-release/acme", "", None)[0] is True
    assert is_paid("https://x.com/world/real-story", "editorial", None)[0] is False
