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
                "no_search_floor", "total_daily_usd", "attr_mult", "attr_text_threshold",
                "slash_mult", "use_llm_judge", "quorum_threshold", "providers", "briefs_pubkey"):
        assert key in p, key
