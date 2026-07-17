import pytest
from fastapi.testclient import TestClient

from herald.services.app import create_app
from herald.services.store import BriefStore, RegistryStore, ResultStore, RevealStore


def _seed_registry():
    return {"version_id": 4, "signature": "deadbeef",
            "outlets": [{"outlet_id": "nyt", "tier": 1, "domains": ["nytimes.com"]}]}


@pytest.fixture
def client(tmp_path):
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        allow_open_writes=True,  # local-dev mode: exercise endpoints without tokens
        registry_store=RegistryStore(str(tmp_path / "draft.json"), _seed_registry),
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


def test_legacy_service_reports_release_version(client):
    assert client.get("/openapi.json").json()["info"]["version"] == "0.1.0"
    assert client.get("/health").json() == {"ok": True, "version": "0.1.0", "legacy": True}


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


def test_registry_draft_stage_flow(client):
    # No draft yet: live is current, draft is null.
    seen = client.get("/admin/registry/draft").json()
    assert seen["live"]["version_id"] == 4 and seen["draft"] is None

    # Stage an add (probation) -> unsigned draft at version 5.
    d = client.post("/admin/registry/outlets",
                    json={"outlet_id": "decrypt", "tier": 3, "domains": ["decrypt.co"]}).json()
    assert d["version_id"] == 5 and "signature" not in d
    assert d["outlets"][-1]["status"] == "probation"

    # Approve clears probation; the draft stays unsigned (signed OFFLINE only).
    d = client.post("/admin/registry/outlets/decrypt/status", json={"status": "active"}).json()
    assert [o for o in d["outlets"] if o["outlet_id"] == "decrypt"][0]["status"] == "active"

    # Reject drops the outlet from the edition.
    client.post("/admin/registry/outlets", json={"outlet_id": "spam", "tier": 3, "domains": ["spam.example"]})
    d = client.post("/admin/registry/outlets/spam/status", json={"status": "rejected"}).json()
    assert all(o["outlet_id"] != "spam" for o in d["outlets"])

    # Discard reverts to live.
    client.post("/admin/registry/discard")
    assert client.get("/admin/registry/draft").json()["draft"] is None


def test_registry_draft_rejects_bad_input(client):
    assert client.post("/admin/registry/outlets", json={"outlet_id": "x", "tier": 9, "domains": ["x.com"]}).status_code == 400
    assert client.post("/admin/registry/outlets", json={"outlet_id": "x", "tier": 1, "domains": []}).status_code == 400
    assert client.post("/admin/registry/outlets/x/status", json={"status": "bogus"}).status_code == 400


def test_registry_draft_admin_gated(tmp_path):
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        admin_token="secret",
        registry_store=RegistryStore(str(tmp_path / "draft.json"), _seed_registry),
    )
    c = TestClient(app)
    assert c.get("/admin/registry/draft").status_code == 401
    assert c.post("/admin/registry/outlets", json={"outlet_id": "x", "tier": 1, "domains": ["x.com"]}).status_code == 401
    ok = c.get("/admin/registry/draft", headers={"X-Admin-Token": "secret"})
    assert ok.status_code == 200


def test_registry_draft_disabled_without_store(tmp_path):
    app = create_app(BriefStore(str(tmp_path / "b.json")), ResultStore(str(tmp_path / "r.json")),
                     allow_open_writes=True)
    c = TestClient(app)
    assert c.get("/admin/registry/draft").status_code == 503


def test_disputes_mirror_write_gated_read_public(tmp_path):
    from herald.services.store import DisputeStore
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        dispute_store=DisputeStore(str(tmp_path / "d.json")),
        disputes_token="dsecret",
    )
    c = TestClient(app)
    d = {"article_id": "https://nyt.com/a", "disputer": "5Disp", "reason": "paid placement"}
    assert c.post("/disputes", json=d).status_code == 401            # write is token-gated
    assert c.post("/disputes", json=d, headers={"X-Disputes-Token": "dsecret"}).status_code == 200
    got = c.get("/disputes")                                          # read is public (disputes are on-chain)
    assert got.status_code == 200 and len(got.json()) == 1 and got.json()[0]["disputer"] == "5Disp"


def test_reveals_gated_on_read_and_write(tmp_path):
    # The nonce is a commit secret, so /reveals fails closed on BOTH read and write.
    app = create_app(
        BriefStore(str(tmp_path / "b.json")),
        ResultStore(str(tmp_path / "r.json")),
        reveal_store=RevealStore(str(tmp_path / "rev.json")),
        reveals_token="vsecret",
    )
    c = TestClient(app)
    rev = {"onchain": "HRLD1|abc", "brief_id": "b1", "nonce": "n"}
    assert c.post("/reveals", json=rev).status_code == 401
    assert c.get("/reveals").status_code == 401
    assert c.post("/reveals", json=rev, headers={"X-Reveals-Token": "vsecret"}).status_code == 200
    got = c.get("/reveals", headers={"X-Reveals-Token": "vsecret"})
    assert got.status_code == 200 and len(got.json()) == 1 and got.json()[0]["onchain"] == "HRLD1|abc"
