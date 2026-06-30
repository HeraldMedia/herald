from herald.services.render import render_board, render_page


def test_board_lists_briefs():
    html = render_board([{"id": "b1", "title": "Coverage push", "tier": 1,
                          "status": "open", "reward_pool": 5000}])
    assert "Coverage push" in html and "Open Briefs" in html


def test_board_escapes_html():
    html = render_board([{"id": "b1", "title": "<script>x</script>", "tier": 1, "status": "open"}])
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_page_lists_articles_and_leaderboard():
    html = render_page(
        articles=[{"url": "https://nyt.com/a", "tier": 1, "hotkey": "hkA", "status": "vesting"}],
        leaderboard=[{"hotkey": "hkA", "articles": 2, "total_usd": 750.0}],
    )
    assert "https://nyt.com/a" in html and "hkA" in html
    assert "Verified Articles" in html and "Leaderboard" in html
