"""Consensus-parameter fingerprint: a short hash of every tunable that must be IDENTICAL across
validators for weights to agree. Logged at startup and attached to published results, so a mixed
fleet (config drift or a staggered deploy) is visible at a glance instead of surfacing as silent
weight divergence."""

import hashlib
import json
import os

from herald.validator.utils import config as cfg


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() in ("1", "true", "yes")


def consensus_params() -> dict:
    return {
        # epochs / timing
        "epoch_len": cfg.EPOCH_LEN,
        "vest_epoch_len": cfg.VEST_EPOCH_LEN,
        "epoch_lag": cfg.HERALD_EPOCH_LAG,
        "vest_epochs": cfg.VEST_EPOCHS,
        "vest_grace": cfg.HERALD_VEST_GRACE_EPOCHS,
        "dead_confirm": cfg.HERALD_DEAD_CONFIRM_EPOCHS,
        "slash_cooldown": cfg.SLASH_COOLDOWN_EPOCHS,
        "max_placement_days": cfg.HERALD_MAX_PLACEMENT_DAYS,
        # scoring
        "base_payout": cfg.HERALD_BASE_PAYOUT_USD,
        "tier_mult": cfg.HERALD_TIER_MULTIPLIER,
        "no_search_floor": cfg.HERALD_NO_SEARCH_FLOOR,
        "emission_mode": "participant_normalized_v1",
        "max_articles_per_miner": cfg.HERALD_MAX_ARTICLES_PER_MINER,
        # Explicitly version the removal of per-claim miner bonding. Older validators omit this
        # key and therefore advertise a different fingerprint instead of silently disagreeing.
        "miner_bond_required": False,
        # attribution evidence
        "attr_mult": cfg.HERALD_ATTR_MULT,
        "attr_min_text_words": cfg.HERALD_ATTR_MIN_TEXT_WORDS,
        "attr_text_threshold": cfg.HERALD_ATTR_TEXT_THRESHOLD,
        "attr_max_window_days": cfg.HERALD_ATTR_MAX_WINDOW_DAYS,
        "snapshot_anchor": cfg.HERALD_SNAPSHOT_ANCHOR,
        # dispute-filer stake eligibility / weight slashing (legacy config names)
        "slash_mult": cfg.SLASH_MULTIPLIER,
        "bond_alpha_per_usd": cfg.HERALD_BOND_ALPHA_PER_USD,
        # judgement tier + disputes (must be enabled identically or weights diverge)
        "use_llm_judge": cfg.HERALD_USE_LLM_JUDGE,
        "ref_model_id": cfg.HERALD_REF_MODEL_ID,
        "llm_provider": cfg.LLM_PROVIDER,
        "llm_provider_ready": bool(
            cfg.CHUTES_API_KEY if cfg.LLM_PROVIDER == "chutes" else cfg.OPENROUTER_API_KEY
        ),
        "dispute_reward_fraction": cfg.HERALD_DISPUTE_REWARD_FRACTION,
        "dispute_window": cfg.HERALD_DISPUTE_WINDOW_EPOCHS,
        # outside-data providers (the set + quorum are consensus per RUNBOOK)
        "quorum_threshold": cfg.HERALD_QUORUM_THRESHOLD,
        "search_top_n": cfg.HERALD_SEARCH_TOP_N,
        "min_body_bytes": cfg.HERALD_MIN_BODY_BYTES,
        "max_body_bytes": cfg.HERALD_MAX_BODY_BYTES,
        "providers": ["http", "scrapingbee"] if cfg.SCRAPINGBEE_API_KEY else ["http"],
        # Search providers (SerpAPI vs Brave return different indexes -> different in_index -> a
        # different search multiplier), so the enabled set must match fleet-wide.
        "search_providers": [n for n, on in (("serpapi", bool(cfg.SERPAPI_API_KEY)),
                                             ("brave", bool(cfg.BRAVE_API_KEY))) if on],
        # Per-outlet fetch strategies need their key on every validator or that outlet forks the
        # fleet: a validator lacking the key rejects the outlet while others verify it. Surface the
        # capability here so a mixed fleet shows as a fingerprint mismatch, not silent divergence.
        "proxy_enabled": bool(cfg.SCRAPINGBEE_API_KEY),
        "api_adapters": ["nyt"] if cfg.HERALD_NYT_API_KEY else [],
        # trust anchors
        "briefs_pubkey": cfg.HERALD_BRIEFS_PUBKEY or "",
        "briefs_max_age": int(os.getenv("HERALD_BRIEFS_MAX_AGE", "900")),
        "require_signed_briefs": _enabled("HERALD_REQUIRE_SIGNED_BRIEFS"),
        "registry_pubkey": os.getenv("HERALD_REGISTRY_PUBKEY", ""),
        "require_signed_registry": _enabled("HERALD_REQUIRE_SIGNED_REGISTRY"),
        "registry_authority_hotkey": os.getenv("HERALD_REGISTRY_AUTHORITY_HOTKEY", ""),
    }


def consensus_fingerprint(params: dict = None) -> str:
    payload = json.dumps(params if params is not None else consensus_params(),
                         sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
