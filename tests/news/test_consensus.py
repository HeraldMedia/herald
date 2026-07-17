from herald.validator.utils.consensus import consensus_fingerprint, consensus_params


def test_fingerprint_deterministic_and_key_order_independent():
    p = {"a": 1, "b": [1, 2], "c": {"x": 0.5}}
    same = {"c": {"x": 0.5}, "b": [1, 2], "a": 1}
    assert consensus_fingerprint(p) == consensus_fingerprint(same)
    assert len(consensus_fingerprint(p)) == 16


def test_any_param_change_changes_fingerprint():
    base = consensus_params()
    fp = consensus_fingerprint(base)
    for key in ("vest_epoch_len", "no_search_floor", "attr_mult", "quorum_threshold"):
        changed = dict(base)
        changed[key] = "DIFFERENT"
        assert consensus_fingerprint(changed) != fp, key


def test_live_params_cover_the_consensus_surface():
    p = consensus_params()
    for key in ("epoch_len", "vest_epoch_len", "vest_epochs", "base_payout", "tier_mult",
                "no_search_floor", "emission_mode", "attr_mult", "attr_text_threshold",
                "miner_bond_required", "slash_mult", "use_llm_judge", "llm_provider",
                "llm_provider_ready",
                "quorum_threshold", "providers", "search_top_n", "min_body_bytes",
                "max_body_bytes", "briefs_pubkey", "briefs_max_age",
                "require_signed_briefs", "registry_pubkey", "require_signed_registry",
                "registry_authority_hotkey"):
        assert key in p, key

    assert p["emission_mode"] == "participant_normalized_v1"
    assert "total_daily_usd" not in p
    assert "burn_uid" not in p
