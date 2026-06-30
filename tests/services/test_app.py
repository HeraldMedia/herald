import pytest
from fastapi.testclient import TestClient

from herald.services.app import create_app
from herald.services.store import BriefStore, ResultStore


@pytest.fixture
def client(tmp_path):
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        allow_open_writes=True,  # local-dev mode: exercise endpoints without tokens
    )
    return TestClient(app)


def test_write_endpoints_closed_without_token_by_default(tmp_path):
    # No token configured and no dev override -> mutating endpoints must fail closed,
    # so a public deployment can't be posted fake "verified" articles.
    app = create_app(BriefStore(str(tmp_path / "b.json")), ResultStore(str(tmp_path / "r.json")))
    c = TestClient(app)
    assert c.post("/results", json={"article_id": "a", "hotkey": "h"}).status_code == 503
    assert c.post("/admin/briefs", json={"title": "x"}).status_code == 503
    assert c.get("/public/articles").status_code == 200  # reads stay open


def test_create_fund_and_list_open_brief(client):
    bid = client.post("/admin/briefs", json={"title": "Push", "tier": 1}).json()["id"]
    assert client.get("/briefs").json() == []          # draft, not open
    client.post(f"/admin/briefs/{bid}/fund")
    assert len(client.get("/briefs").json()) == 1
    assert len(client.get("/api/v2/validator/briefs").json()["items"]) == 1


def test_fund_unknown_brief_404(client):
    assert client.post("/admin/briefs/nope/fund").status_code == 404


def test_results_proof_and_leaderboard(client):
    client.post("/results", json={"article_id": "a", "hotkey": "hkA", "brief_id": "b1", "tier": 1, "usd": 500.0})
    client.post("/results", json={"article_id": "b", "hotkey": "hkB", "brief_id": "b1", "tier": 2, "usd": 250.0})
    assert len(client.get("/public/articles").json()) == 2
    board = client.get("/public/leaderboard").json()
    assert board[0]["hotkey"] == "hkA"
    export = client.get("/reporting/export").json()
    assert "articles" in export and "leaderboard" in export


def test_html_board_and_page(client):
    bid = client.post("/admin/briefs", json={"title": "Coverage push", "tier": 1}).json()["id"]
    client.post(f"/admin/briefs/{bid}/fund")
    client.post("/results", json={"article_id": "a", "hotkey": "hkA", "url": "https://nyt.com/a",
                                  "tier": 1, "status": "vesting", "usd": 500.0})
    board = client.get("/board")
    assert board.status_code == 200 and "Coverage push" in board.text
    page = client.get("/page")
    assert page.status_code == 200 and "https://nyt.com/a" in page.text and "hkA" in page.text


def test_results_token_enforced(tmp_path):
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        results_token="rsecret",
    )
    c = TestClient(app)
    assert c.post("/results", json={"article_id": "a", "hotkey": "h"}).status_code == 401
    ok = c.post("/results", json={"article_id": "a", "hotkey": "h"},
                headers={"X-Results-Token": "rsecret"})
    assert ok.status_code == 200


def test_admin_token_enforced(tmp_path):
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        admin_token="secret",
    )
    c = TestClient(app)
    assert c.post("/admin/briefs", json={"title": "x"}).status_code == 401
    ok = c.post("/admin/briefs", json={"title": "x"}, headers={"X-Admin-Token": "secret"})
    assert ok.status_code == 200
