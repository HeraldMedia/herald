"""Consensus-parameter fingerprint: a short hash of every tunable that must be IDENTICAL across
validators for weights to agree. Logged at startup and attached to published results, so a mixed
fleet (config drift or a staggered deploy) is visible at a glance instead of surfacing as silent
weight divergence."""

import hashlib
import json

from herald.validator.utils import config as cfg


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
        "total_daily_usd": cfg.HERALD_TOTAL_DAILY_USD,
        "burn_uid": cfg.SUBNET_BURN_UID,
        "max_articles_per_miner": cfg.HERALD_MAX_ARTICLES_PER_MINER,
        # attribution evidence
        "attr_mult": cfg.HERALD_ATTR_MULT,
        "attr_min_text_words": cfg.HERALD_ATTR_MIN_TEXT_WORDS,
        "attr_text_threshold": cfg.HERALD_ATTR_TEXT_THRESHOLD,
        "attr_max_window_days": cfg.HERALD_ATTR_MAX_WINDOW_DAYS,
        "snapshot_anchor": cfg.HERALD_SNAPSHOT_ANCHOR,
        # bonds / slashing
        "slash_mult": cfg.SLASH_MULTIPLIER,
        "bond_alpha_per_usd": cfg.HERALD_BOND_ALPHA_PER_USD,
        "min_alpha_stake": cfg.HERALD_MIN_ALPHA_STAKE_THRESHOLD,
        # judgement tier + disputes (must be enabled identically or weights diverge)
        "use_llm_judge": cfg.HERALD_USE_LLM_JUDGE,
        "ref_model_id": cfg.HERALD_REF_MODEL_ID,
        "dispute_reward_fraction": cfg.HERALD_DISPUTE_REWARD_FRACTION,
        "dispute_window": cfg.HERALD_DISPUTE_WINDOW_EPOCHS,
        # outside-data providers (the set + quorum are consensus per RUNBOOK)
        "quorum_threshold": cfg.HERALD_QUORUM_THRESHOLD,
        "providers": ["http", "scrapingbee"] if cfg.SCRAPINGBEE_API_KEY else ["http"],
        # Per-outlet fetch strategies need their key on every validator or that outlet forks the
        # fleet: a validator lacking the key rejects the outlet while others verify it. Surface the
        # capability here so a mixed fleet shows as a fingerprint mismatch, not silent divergence.
        "proxy_enabled": bool(cfg.SCRAPINGBEE_API_KEY),
        "api_adapters": ["nyt"] if cfg.HERALD_NYT_API_KEY else [],
        # trust anchors
        "briefs_pubkey": cfg.HERALD_BRIEFS_PUBKEY or "",
    }


def consensus_fingerprint(params: dict = None) -> str:
    payload = json.dumps(params if params is not None else consensus_params(),
                         sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
