import os

from herald.services.store import BriefStore, RegistryStore, ResultStore


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


def test_result_upsert_by_article_id(tmp_path):
    r = ResultStore(str(tmp_path / "results.json"))
    r.add({"article_id": "a", "hotkey": "hkA", "usd": 100.0, "status": "vesting"})
    r.add({"article_id": "a", "hotkey": "hkA", "usd": 500.0, "status": "completed"})
    assert len(r.articles()) == 1 and r.articles()[0]["status"] == "completed"


def test_leaderboard_skips_results_without_hotkey(tmp_path):
    r = ResultStore(str(tmp_path / "results.json"))
    r.add({"article_id": "a", "hotkey": "hkA", "usd": 100.0})
    r.add({"article_id": "b", "usd": 50.0})  # malformed: no hotkey -> must not crash the page
    board = r.leaderboard()
    assert [row["hotkey"] for row in board] == ["hkA"]


def test_result_store_caps_growth(tmp_path):
    r = ResultStore(str(tmp_path / "results.json"), max_items=2)
    for i in range(4):
        r.add({"article_id": str(i), "hotkey": "h", "usd": 1.0})
    arts = r.articles()
    assert [a["article_id"] for a in arts] == ["2", "3"]  # bounded; oldest evicted


def test_store_save_is_atomic(tmp_path):
    path = str(tmp_path / "results.json")
    ResultStore(path).add({"article_id": "a", "hotkey": "h", "usd": 1.0})
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == [] and os.path.exists(path)
    assert len(ResultStore(path).articles()) == 1  # reloads cleanly


def _live(version=5):
    return {"version_id": version, "signature": "deadbeef",
            "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["nytimes.com"]}]}


def test_registry_draft_seeds_from_live_unsigned(tmp_path):
    s = RegistryStore(str(tmp_path / "draft.json"), lambda: _live(5))
    assert s.draft() is None  # no draft yet -> live is current
    d = s.add_outlet("decrypt", 3, ["decrypt.co"])
    assert d["version_id"] == 6 and "signature" not in d  # bumped once over live, unsigned
    assert d["outlets"][-1] == {"outlet_id": "decrypt", "tier": 3, "domains": ["decrypt.co"], "status": "probation"}


def test_registry_approve_and_reject(tmp_path):
    s = RegistryStore(str(tmp_path / "draft.json"), lambda: _live(5))
    s.add_outlet("decrypt", 3, ["decrypt.co"])
    s.add_outlet("spam", 3, ["spam.example"])
    s.set_status("decrypt", "active")
    s.set_status("spam", "rejected")
    ids = {o["outlet_id"]: o for o in s.draft()["outlets"]}
    assert ids["decrypt"]["status"] == "active"  # probation cleared
    assert "spam" not in ids  # rejected -> dropped from the edition


def test_registry_draft_persists_and_discards(tmp_path):
    path = str(tmp_path / "draft.json")
    RegistryStore(path, lambda: _live(5)).add_outlet("decrypt", 3, ["decrypt.co"])
    assert RegistryStore(path, lambda: _live(5)).draft()["version_id"] == 6  # reloads from disk
    s = RegistryStore(path, lambda: _live(5))
    s.discard()
    assert s.draft() is None and not os.path.exists(path)
