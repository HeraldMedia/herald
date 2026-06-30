"""Minimal server-rendered HTML for the brief board and public proof page."""

import html
from urllib.parse import urlsplit

_STYLE = "<style>body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse}" \
         "td,th{border:1px solid #ccc;padding:4px 8px}</style>"


def _esc(value) -> str:
    return html.escape(str(value))


def _article_cell(url) -> str:
    # only make http(s) URLs clickable; never emit a javascript:/data: href
    if urlsplit(str(url)).scheme in ("http", "https"):
        return f"<a href=\"{_esc(url)}\">{_esc(url)}</a>"
    return _esc(url)


def render_board(briefs) -> str:
    rows = "".join(
        f"<tr><td>{_esc(b.get('title', ''))}</td><td>{_esc(b.get('tier', ''))}</td>"
        f"<td>{_esc(b.get('status', ''))}</td><td>{_esc(b.get('reward_pool', ''))}</td></tr>"
        for b in briefs
    )
    return (
        f"<html><head><title>Herald Briefs</title>{_STYLE}</head><body>"
        f"<h1>Open Briefs</h1><table>"
        f"<tr><th>Title</th><th>Tier</th><th>Status</th><th>Reward pool</th></tr>"
        f"{rows}</table></body></html>"
    )


def render_page(articles, leaderboard) -> str:
    arows = "".join(
        f"<tr><td>{_article_cell(a.get('url', ''))}</td>"
        f"<td>{_esc(a.get('tier', ''))}</td><td>{_esc(a.get('hotkey', ''))}</td>"
        f"<td>{_esc(a.get('status', ''))}</td></tr>"
        for a in articles
    )
    lrows = "".join(
        f"<tr><td>{i + 1}</td><td>{_esc(r['hotkey'])}</td>"
        f"<td>{_esc(r['articles'])}</td><td>{_esc(r['total_usd'])}</td></tr>"
        for i, r in enumerate(leaderboard)
    )
    return (
        f"<html><head><title>Herald — Verified Media</title>{_STYLE}</head><body>"
        f"<h1>Verified Articles</h1><table>"
        f"<tr><th>Article</th><th>Tier</th><th>Miner</th><th>Status</th></tr>{arows}</table>"
        f"<h1>Leaderboard</h1><table>"
        f"<tr><th>#</th><th>Miner</th><th>Articles</th><th>Total USD</th></tr>{lrows}</table>"
        f"</body></html>"
    )
