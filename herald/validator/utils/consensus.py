"""Consensus-parameter fingerprint: a short hash of every tunable that must be IDENTICAL across
validators for weights to agree. Logged at startup and attached to published results, so a mixed
fleet (config drift or a staggered deploy) is visible at a glance instead of surfacing as silent
weight divergence."""

import hashlib
import json
import os

from herald.validator.news import pricing
from herald.validator.news.emission import BURN_UID
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
        # scoring
        "base_payout": cfg.HERALD_BASE_PAYOUT_USD,
        "tier_mult": cfg.HERALD_TIER_MULTIPLIER,
        "no_search_floor": cfg.HERALD_NO_SEARCH_FLOOR,
        # weights: the share owed to contributors is verified USD over the USD value of the day's
        # miner emission. With burn_unearned the incentive hotkey's weight is that share and UID 0
        # receives the rest; without it the incentive hotkey receives all the weight and the epoch
        # snapshot states the share (contributor_share_ppb).
        "emission_mode": "incentive_burn_v1",
        "burn_uid": BURN_UID,
        "burn_unearned": cfg.HERALD_BURN_UNEARNED,
        "incentive_hotkey": cfg.HERALD_INCENTIVE_HOTKEY,
        "price_source": pricing.PRICE_SOURCE,
        "miner_emission_share": pricing.MINER_EMISSION_SHARE,
        "blocks_per_day": pricing.BLOCKS_PER_DAY,
        # submission intake and verification. v2: exact publication times are compared with the
        # upload time, the earliest matching draft of an article is credited, and articles are
        # verified in order of their earliest upload.
        "intake": "backend_submissions_draft_match_v2",
        "draft_match_threshold": cfg.HERALD_DRAFT_MATCH_THRESHOLD,
        "publish_buffer_days": cfg.HERALD_PUBLISH_BUFFER_DAYS,
        "max_article_age_days": cfg.HERALD_MAX_ARTICLE_AGE_DAYS,
        "max_submissions_per_epoch": cfg.HERALD_MAX_SUBMISSIONS_PER_EPOCH,
        "max_candidates_per_article": cfg.HERALD_MAX_CANDIDATES_PER_ARTICLE,
        # judgement tier (must be enabled identically or weights diverge)
        "use_llm_judge": cfg.HERALD_USE_LLM_JUDGE,
        "ref_model_id": cfg.HERALD_REF_MODEL_ID,
        "llm_provider": cfg.LLM_PROVIDER,
        "llm_provider_ready": bool(
            cfg.CHUTES_API_KEY if cfg.LLM_PROVIDER == "chutes" else cfg.OPENROUTER_API_KEY
        ),
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
