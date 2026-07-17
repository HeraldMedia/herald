import os


def require_legacy_brief_board(env=None) -> None:
    env = os.environ if env is None else env
    if str(env.get("HERALD_PRODUCTION", "")).lower() in ("1", "true", "yes"):
        raise RuntimeError("legacy JSON Brief Board cannot run in production; use herald-backend")
    if str(env.get("HERALD_ENABLE_LEGACY_BRIEF_BOARD", "")).lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "legacy JSON Brief Board is disabled; use herald-backend or explicitly set "
            "HERALD_ENABLE_LEGACY_BRIEF_BOARD=true for development/migration"
        )
