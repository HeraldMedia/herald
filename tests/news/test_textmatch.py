from types import SimpleNamespace
from datetime import datetime, timezone

from herald.validator.news.textmatch import containment, grade_evidence, normalize_text

DRAFT = ("Herald, the verified media placement subnet on Bittensor, today announced its public "
         "pilot. “Earned coverage should be provable, not promised,” said Jane Doe, head of "
         "communications. The pilot pays miners only for articles that pass an automated oracle.")

BRIEF = {"id": "b1", "title": "Launch coverage", "messages": ["verified placement", "commit-reveal"]}


def fr(text="", author=None, published="2026-07-12"):
    ts = datetime.fromisoformat(published + "T12:00:00+00:00").timestamp() if published else None
    return SimpleNamespace(text=text, author=author, published_ts=ts)


def test_normalize_strips_punctuation_case_and_unicode():
    assert normalize_text("“Earned  coverage” — should, be PROVABLE!") == "earned coverage should be provable"


def test_containment_exact_and_unrelated():
    article = "Intro paragraph. " + DRAFT + " Closing thoughts from the editor."
    assert containment(DRAFT, article) == 1.0
    assert containment(DRAFT, "A completely different story about markets and weather.") == 0.0


def test_containment_survives_editing():
    # A journalist trims and reorders but keeps most sentences: still well above threshold.
    edited = ("Herald, the verified media placement subnet on Bittensor, today announced its public pilot. "
              "The pilot pays miners only for articles that pass an automated oracle, the company said.")
    assert containment(edited, DRAFT) > 0.6


def test_short_quote_path():
    quote = "Earned coverage should be provable, not promised"
    assert containment(quote, DRAFT) == 1.0
    assert containment(quote, "unrelated text entirely") == 0.0


def test_grade_level2_on_text_proof():
    level, detail = grade_evidence({"text": DRAFT}, fr(text="pre " + DRAFT + " post"), BRIEF)
    assert level == 2 and detail["containment"] == 1.0


def test_grade_rejects_brief_copy_as_evidence():
    # Committing the (public) brief copy proves nothing.
    text = "Launch coverage verified placement commit-reveal " * 3
    level, _ = grade_evidence({"text": text}, fr(text=text), BRIEF)
    assert level == 0


def test_grade_short_text_not_level2():
    level, _ = grade_evidence({"text": "too short quote"}, fr(text="too short quote"), BRIEF)
    assert level == 0


def test_grade_level1_author_and_window():
    ev = {"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]}
    level, detail = grade_evidence(ev, fr(author="Jane Doe"), BRIEF)
    assert level == 1 and detail["author"] == "Jane Doe"
    # author normalization: case/punctuation-insensitive
    level, _ = grade_evidence(ev, fr(author="JANE  DOE"), BRIEF)
    assert level == 1


def test_grade_author_alone_or_window_alone_is_level0():
    assert grade_evidence({"author": "Jane Doe"}, fr(author="Jane Doe"), BRIEF)[0] == 0
    assert grade_evidence({"window": ["2026-07-10", "2026-07-15"]}, fr(), BRIEF)[0] == 0
    assert grade_evidence({"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]},
                          fr(author="John Smith"), BRIEF)[0] == 0


def test_grade_loose_or_missed_window_fails():
    ev = {"author": "Jane Doe", "window": ["2026-07-01", "2026-07-30"]}  # 29-day span > max 7
    assert grade_evidence(ev, fr(author="Jane Doe"), BRIEF)[0] == 0
    ev = {"author": "Jane Doe", "window": ["2026-07-01", "2026-07-05"]}  # published outside
    assert grade_evidence(ev, fr(author="Jane Doe", published="2026-07-12"), BRIEF)[0] == 0


def test_grade_no_published_date_fails_window():
    ev = {"author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]}
    assert grade_evidence(ev, SimpleNamespace(text="", author="Jane Doe", published_ts=None), BRIEF)[0] == 0


def test_text_beats_detail_when_both_present():
    ev = {"text": DRAFT, "author": "Jane Doe", "window": ["2026-07-10", "2026-07-15"]}
    assert grade_evidence(ev, fr(text=DRAFT, author="Jane Doe"), BRIEF)[0] == 2
    # text misses but detail holds -> level 1
    assert grade_evidence(ev, fr(text="unrelated body", author="Jane Doe"), BRIEF)[0] == 1
