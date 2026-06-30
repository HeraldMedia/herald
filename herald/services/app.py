"""Herald supporting services: brief board, operator admin, public proof page, reporting."""

import json
import os
import secrets
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .render import render_board, render_page
from .store import BriefStore, ResultStore

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
               cors_origins=None, allow_open_writes: bool = False) -> FastAPI:
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

    @app.get("/briefs")
    def miner_briefs():
        return brief_store.open_briefs()

    @app.get("/api/v2/validator/briefs")
    def validator_briefs():
        return {"items": brief_store.open_briefs()}

    @app.post("/results")
    def ingest_result(item: dict = Body(...), x_results_token: str = Header(None)):
        _check(results_token, x_results_token)
        result_store.add(item)
        return {"ok": True}

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
)
