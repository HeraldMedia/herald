from herald.validator.news import pricing
from herald.validator.utils import config as cfg
from herald.validator.utils.consensus import consensus_fingerprint, consensus_params


def test_fingerprint_deterministic_and_key_order_independent():
    p = {"a": 1, "b": [1, 2], "c": {"x": 0.5}}
    same = {"c": {"x": 0.5}, "b": [1, 2], "a": 1}
    assert consensus_fingerprint(p) == consensus_fingerprint(same)
    assert len(consensus_fingerprint(p)) == 16


def test_any_param_change_changes_fingerprint():
    base = consensus_params()
    fp = consensus_fingerprint(base)
    for key in ("vest_epoch_len", "no_search_floor", "quorum_threshold", "incentive_hotkey",
                "burn_unearned", "miner_emission_share", "blocks_per_day", "price_source", "intake",
                "draft_match_threshold", "max_submissions_per_epoch", "max_candidates_per_article"):
        changed = dict(base)
        changed[key] = "DIFFERENT"
        assert consensus_fingerprint(changed) != fp, key


def test_live_params_cover_the_consensus_surface():
    p = consensus_params()
    for key in ("epoch_len", "vest_epoch_len", "vest_epochs", "base_payout", "tier_mult",
                "no_search_floor", "emission_mode", "burn_uid", "burn_unearned",
                "incentive_hotkey", "price_source",
                "miner_emission_share", "blocks_per_day", "intake", "draft_match_threshold",
                "publish_buffer_days", "max_article_age_days", "max_submissions_per_epoch",
                "max_candidates_per_article", "use_llm_judge", "llm_provider", "llm_provider_ready",
                "quorum_threshold", "providers", "search_top_n", "min_body_bytes",
                "max_body_bytes", "briefs_pubkey", "briefs_max_age",
                "require_signed_briefs", "registry_pubkey", "require_signed_registry",
                "registry_authority_hotkey"):
        assert key in p, key

    assert p["emission_mode"] == "incentive_burn_v1"
    assert p["burn_uid"] == 0
    assert p["burn_unearned"] is cfg.HERALD_BURN_UNEARNED
    assert p["incentive_hotkey"] == cfg.HERALD_INCENTIVE_HOTKEY
    assert p["price_source"] == "chain_spot_alpha_x_coingecko_tao_usd_v1" == pricing.PRICE_SOURCE
    assert p["miner_emission_share"] == 0.41
    assert p["blocks_per_day"] == 7200
    assert p["intake"] == "backend_submissions_draft_match_v2"
    assert p["draft_match_threshold"] == cfg.HERALD_DRAFT_MATCH_THRESHOLD
    assert p["publish_buffer_days"] == cfg.HERALD_PUBLISH_BUFFER_DAYS
    assert p["max_article_age_days"] == cfg.HERALD_MAX_ARTICLE_AGE_DAYS
    assert p["max_submissions_per_epoch"] == cfg.HERALD_MAX_SUBMISSIONS_PER_EPOCH
    assert p["max_candidates_per_article"] == cfg.HERALD_MAX_CANDIDATES_PER_ARTICLE


def test_keys_for_removed_rules_are_absent():
    p = consensus_params()
    for key in ("slash_cooldown", "max_placement_days", "max_articles_per_miner",
                "miner_bond_required", "attr_mult", "attr_min_text_words", "attr_text_threshold",
                "attr_max_window_days", "snapshot_anchor", "slash_mult", "bond_alpha_per_usd",
                "dispute_reward_fraction", "dispute_window", "mechanism_id", "value_rule",
                "total_daily_usd"):
        assert key not in p, key


def test_incentive_hotkey_and_draft_match_threshold_move_the_fingerprint(monkeypatch):
    fp = consensus_fingerprint()
    monkeypatch.setattr(cfg, "HERALD_INCENTIVE_HOTKEY", "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY")
    with_hotkey = consensus_fingerprint()
    assert with_hotkey != fp
    monkeypatch.setattr(cfg, "HERALD_DRAFT_MATCH_THRESHOLD", cfg.HERALD_DRAFT_MATCH_THRESHOLD + 0.1)
    assert consensus_fingerprint() != with_hotkey


def test_burning_unearned_weight_is_off_by_default_and_moves_the_fingerprint(monkeypatch):
    monkeypatch.delenv("HERALD_BURN_UNEARNED", raising=False)
    assert cfg._env_flag("HERALD_BURN_UNEARNED") is False
    for value, expected in (("true", True), ("TRUE ", True), ("1", True), ("yes", True),
                            ("false", False), ("0", False), ("", False)):
        monkeypatch.setenv("HERALD_BURN_UNEARNED", value)
        assert cfg._env_flag("HERALD_BURN_UNEARNED") is expected, value

    monkeypatch.setattr(cfg, "HERALD_BURN_UNEARNED", False)
    off = consensus_params()
    monkeypatch.setattr(cfg, "HERALD_BURN_UNEARNED", True)
    on = consensus_params()
    assert (off["burn_unearned"], on["burn_unearned"]) == (False, True)
    assert {key for key in off if off[key] != on[key]} == {"burn_unearned"}
    assert consensus_fingerprint(off) != consensus_fingerprint(on)
    # Adding the setting moves the fingerprint in both modes, so a validator without it shows as
    # a mismatch whichever mode the fleet runs.
    before = {key: value for key, value in off.items() if key != "burn_unearned"}
    assert consensus_fingerprint(before) not in (consensus_fingerprint(off),
                                                 consensus_fingerprint(on))


def test_the_per_epoch_and_per_article_caps_move_the_fingerprint(monkeypatch):
    fp = consensus_fingerprint()
    monkeypatch.setattr(cfg, "HERALD_MAX_CANDIDATES_PER_ARTICLE", cfg.HERALD_MAX_CANDIDATES_PER_ARTICLE + 1)
    with_candidates = consensus_fingerprint()
    assert with_candidates != fp
    monkeypatch.setattr(cfg, "HERALD_MAX_SUBMISSIONS_PER_EPOCH", cfg.HERALD_MAX_SUBMISSIONS_PER_EPOCH + 1)
    assert consensus_fingerprint() != with_candidates


def test_intake_rules_version_moves_the_fingerprint():
    current = consensus_params()
    previous = {key: value for key, value in current.items() if key != "max_candidates_per_article"}
    previous["intake"] = "backend_submissions_draft_match_v1"
    assert consensus_fingerprint(previous) != consensus_fingerprint(current)
    assert consensus_fingerprint({**current, "intake": previous["intake"]}) != consensus_fingerprint(current)
