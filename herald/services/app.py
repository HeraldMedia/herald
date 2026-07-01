"""Herald supporting services: brief board, operator admin, public proof page, reporting."""

import json
import os
import secrets
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .render import render_board, render_page
from .store import BriefStore, DisputeStore, FundingStore, RegistryStore, ResultStore, RevealStore

# Outlet registry for public stats + serving (the signed file if configured, else the seed).
_SEED_REGISTRY = Path(__file__).resolve().parents[1] / "validator" / "news" / "outlets.seed.json"


def _load_registry() -> dict:
    path = os.getenv("HERALD_REGISTRY_PATH")
    p = Path(path) if path else _SEED_REGISTRY
    if not p.exists():
        p = _SEED_REGISTRY
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"outlets": [], "version_id": 0}


def create_app(brief_store: BriefStore, result_store: ResultStore,
               admin_token: str = None, results_token: str = None,
               cors_origins=None, allow_open_writes: bool = False,
               reveal_store: RevealStore = None, reveals_token: str = None,
               registry_store: RegistryStore = None,
               dispute_store: DisputeStore = None, disputes_token: str = None,
               briefs_privkey: str = None,
               funding_store: FundingStore = None, funding_token: str = None) -> FastAPI:
    app = FastAPI(title="Herald Brief Board")

    # Public read endpoints are consumed cross-origin by the landing page.
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def _check(expected, token):
        if not expected:
            if allow_open_writes:
                return  # local dev only
            raise HTTPException(status_code=503,
                                detail="write endpoint disabled: set its token (or HERALD_ALLOW_OPEN_WRITES for local dev)")
        if not (token and secrets.compare_digest(token, expected)):
            raise HTTPException(status_code=401, detail="unauthorized")

    def _check_admin(token):
        _check(admin_token, token)

    @app.post("/admin/briefs")
    def create_brief(brief: dict = Body(...), x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        return brief_store.create(brief)

    @app.post("/admin/briefs/{brief_id}/fund")
    def fund_brief(brief_id: str, x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        if brief_store.get(brief_id) is None:
            raise HTTPException(status_code=404, detail="no such brief")
        return brief_store.fund(brief_id)

    @app.post("/admin/briefs/{brief_id}/boost")
    def set_brief_boost(brief_id: str, brief: dict = Body(...), x_admin_token: str = Header(None)):
        # The operator sets a brief's boost from the funder's confirmed α holding; the validator
        # independently clamps it to [1, HERALD_FUND_BOOST_MAX].
        _check_admin(x_admin_token)
        try:
            boost = float(brief.get("boost"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="boost must be a number")
        updated = brief_store.set_boost(brief_id, boost)
        if updated is None:
            raise HTTPException(status_code=404, detail="no such brief")
        return updated

    @app.post("/funding")
    def ingest_funding(item: dict = Body(...), x_funding_token: str = Header(None)):
        _check(funding_token, x_funding_token)
        if funding_store is not None:
            funding_store.add(item)
        return {"ok": True}

    @app.get("/funding")
    def list_funding():
        return funding_store.all() if funding_store is not None else []

    @app.get("/briefs")
    def miner_briefs():
        return brief_store.open_briefs()

    @app.get("/api/v2/validator/briefs")
    def validator_briefs():
        # Sign the validator feed (when a key is configured) so a brief's boost is operator-
        # attributable. The key is online here (briefs are dynamic) — see signed_briefs.py caveat.
        payload = {"items": brief_store.open_briefs()}
        if briefs_privkey:
            from herald.validator.news.signed_briefs import sign_briefs
            payload = sign_briefs(payload, briefs_privkey)
        return payload

    @app.post("/results")
    def ingest_result(item: dict = Body(...), x_results_token: str = Header(None)):
        _check(results_token, x_results_token)
        result_store.add(item)
        return {"ok": True}

    @app.post("/reveals")
    def ingest_reveal(item: dict = Body(...), x_reveals_token: str = Header(None)):
        _check(reveals_token, x_reveals_token)
        if reveal_store is not None:
            reveal_store.add(item)
        return {"ok": True}

    @app.get("/reveals")
    def list_reveals(x_reveals_token: str = Header(None)):
        _check(reveals_token, x_reveals_token)
        return reveal_store.all() if reveal_store is not None else []

    # Display mirror of on-chain disputes (validators read disputes from chain; this is UX only).
    @app.post("/disputes")
    def ingest_dispute(item: dict = Body(...), x_disputes_token: str = Header(None)):
        _check(disputes_token, x_disputes_token)
        if dispute_store is not None:
            dispute_store.add(item)
        return {"ok": True}

    @app.get("/disputes")
    def list_disputes():
        return dispute_store.all() if dispute_store is not None else []

    @app.get("/public/articles")
    def public_articles():
        return result_store.articles()

    @app.get("/public/leaderboard")
    def public_leaderboard():
        return result_store.leaderboard()

    @app.get("/public/stats")
    def public_stats():
        items = result_store.articles()
        outlets = _load_registry().get("outlets", [])
        return {
            "verified_placements": len(items),
            "active_miners": len({i.get("hotkey") for i in items if i.get("hotkey")}),
            "approved_outlets": len(outlets),
            "tier1_outlets": sum(1 for o in outlets if int(o.get("tier", 0)) == 1),
        }

    @app.get("/registry/outlets.json")
    def registry_outlets():
        return _load_registry()

    # ── Outlet-registry draft (operator staging; signed OFFLINE, never here) ──────────
    def _require_registry():
        if registry_store is None:
            raise HTTPException(status_code=503, detail="registry draft store not configured")

    @app.get("/admin/registry/draft")
    def registry_draft(x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        _require_registry()
        return {"live": registry_store.live(), "draft": registry_store.draft()}

    @app.post("/admin/registry/outlets")
    def registry_add_outlet(item: dict = Body(...), x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        _require_registry()
        outlet_id = item.get("outlet_id")
        domains = item.get("domains")
        try:
            tier = int(item.get("tier"))
        except (TypeError, ValueError):
            tier = 0
        if not outlet_id or tier not in (1, 2, 3) or not isinstance(domains, list) or not domains:
            raise HTTPException(status_code=400, detail="outlet_id, tier (1-3) and non-empty domains[] required")
        return registry_store.add_outlet(str(outlet_id), tier, [str(d) for d in domains])

    @app.post("/admin/registry/outlets/{outlet_id}/status")
    def registry_set_status(outlet_id: str, item: dict = Body(...), x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        _require_registry()
        status = item.get("status")
        if status not in ("active", "rejected"):
            raise HTTPException(status_code=400, detail="status must be 'active' or 'rejected'")
        return registry_store.set_status(outlet_id, status)

    @app.post("/admin/registry/discard")
    def registry_discard(x_admin_token: str = Header(None)):
        _check_admin(x_admin_token)
        _require_registry()
        registry_store.discard()
        return {"ok": True}

    @app.get("/reporting/export")
    def reporting_export():
        return {"articles": result_store.articles(), "leaderboard": result_store.leaderboard()}

    @app.get("/board", response_class=HTMLResponse)
    def board_page():
        return render_board(brief_store.open_briefs())

    @app.get("/page", response_class=HTMLResponse)
    def proof_page():
        return render_page(result_store.articles(), result_store.leaderboard())

    return app


app = create_app(
    BriefStore(os.getenv("HERALD_BRIEF_STORE", "brief_store.json")),
    ResultStore(os.getenv("HERALD_RESULT_STORE", "result_store.json")),
    admin_token=os.getenv("HERALD_ADMIN_TOKEN"),
    results_token=os.getenv("HERALD_RESULTS_TOKEN"),
    cors_origins=os.getenv("HERALD_CORS_ORIGINS"),
    allow_open_writes=os.getenv("HERALD_ALLOW_OPEN_WRITES", "").lower() in ("1", "true", "yes"),
    reveal_store=RevealStore(os.getenv("HERALD_REVEAL_STORE", "reveal_store.json")),
    reveals_token=os.getenv("HERALD_REVEALS_TOKEN"),
    registry_store=RegistryStore(os.getenv("HERALD_REGISTRY_DRAFT_STORE", "registry_draft.json"), _load_registry),
    dispute_store=DisputeStore(os.getenv("HERALD_DISPUTE_STORE", "dispute_store.json")),
    disputes_token=os.getenv("HERALD_DISPUTES_TOKEN"),
    briefs_privkey=os.getenv("HERALD_BRIEFS_PRIVKEY"),
    funding_store=FundingStore(os.getenv("HERALD_FUNDING_STORE", "funding_store.json")),
    funding_token=os.getenv("HERALD_FUNDING_TOKEN"),
)
