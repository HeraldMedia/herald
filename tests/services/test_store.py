from herald.services.store import BriefStore, ResultStore


def test_brief_create_is_draft_unfunded(tmp_path):
    s = BriefStore(str(tmp_path / "briefs.json"))
    b = s.create({"title": "Coverage push", "tier": 1, "keywords": ["bittensor"],
                  "start_date": "2026-07-01", "end_date": "2026-07-31", "reward_pool": 5000})
    assert b["id"] and b["status"] == "draft" and b["funded"] is False
    assert s.open_briefs() == []  # not funded yet


def test_fund_opens_brief(tmp_path):
    s = BriefStore(str(tmp_path / "briefs.json"))
    b = s.create({"title": "x", "tier": 1})
    s.fund(b["id"])
    opened = s.open_briefs()
    assert len(opened) == 1 and opened[0]["status"] == "open" and opened[0]["funded"] is True


def test_brief_persists(tmp_path):
    path = str(tmp_path / "briefs.json")
    bid = BriefStore(path).create({"title": "x"})["id"]
    BriefStore(path).fund(bid)
    assert len(BriefStore(path).open_briefs()) == 1


def test_results_articles_and_leaderboard(tmp_path):
    r = ResultStore(str(tmp_path / "results.json"))
    r.add({"article_id": "a", "hotkey": "hkA", "brief_id": "b1", "tier": 1, "usd": 500.0})
    r.add({"article_id": "b", "hotkey": "hkA", "brief_id": "b1", "tier": 2, "usd": 250.0})
    r.add({"article_id": "c", "hotkey": "hkB", "brief_id": "b1", "tier": 1, "usd": 500.0})
    assert len(r.articles()) == 3
    board = r.leaderboard()
    assert board[0]["hotkey"] == "hkA" and board[0]["articles"] == 2 and board[0]["total_usd"] == 750.0
    assert board[1]["hotkey"] == "hkB"
