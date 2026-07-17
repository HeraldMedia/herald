import pytest


def test_legacy_brief_board_requires_explicit_opt_in():
    from herald.services.legacy_guard import require_legacy_brief_board

    with pytest.raises(RuntimeError, match="legacy JSON Brief Board is disabled"):
        require_legacy_brief_board({})
    require_legacy_brief_board({"HERALD_ENABLE_LEGACY_BRIEF_BOARD": "true"})
    with pytest.raises(RuntimeError, match="cannot run in production"):
        require_legacy_brief_board({
            "HERALD_ENABLE_LEGACY_BRIEF_BOARD": "true",
            "HERALD_PRODUCTION": "true",
        })
