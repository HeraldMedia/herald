"""Herald supporting services: brief board, operator admin, public proof page, reporting."""

import os
import secrets

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .render import render_board, render_page
from .store import BriefStore, ResultStore


def create_app(brief_store: BriefStore, result_store: ResultStore,
               admin_token: str = None, results_token: str = None) -> FastAPI:
    app = FastAPI(title="Herald Brief Board")

    def _check(expected, token):
        if expected and not (token and secrets.compare_digest(token, expected)):
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
)
