from herald.validator.news.quorum_check import validate_payloads


def _requirements():
    return {
        "target_epoch": 1052,
        "articles": [{
            "article_id": "article-1", "hotkey": "miner-1", "brief_id": "brief-1",
            "outlet_id": "venturebeat", "url": "https://venturebeat.com/news/article-1",
            "min_earned_microusd": 10_000_000,
        }],
        "miners": ["miner-1"],
        "briefs": [{"brief_id": "brief-1", "min_pool_spent_microusd": 10_000_000}],
    }


def test_confirmed_quorum_requires_expected_articles_miners_rewards_and_economics():
    decision = {
        "epoch": 1052, "status": "confirmed", "attestation_count": 2,
        "required_attestations": 2,
    }
    articles = [{
        "article_id": "article-1", "hotkey": "miner-1", "brief_id": "brief-1",
        "outlet_id": "venturebeat", "url": "https://venturebeat.com/news/article-1",
        "confirmation_status": "confirmed", "earned_microusd": 10_000_000,
    }]
    leaderboard = [{
        "hotkey": "miner-1", "articles": 1, "total_usd": 10,
        "daily_reward_usd": 10, "intended_weight": 1,
    }]
    economics = {"brief-1": {
        "confirmation_status": "confirmed", "epoch": 1052,
        "pool_spent_microusd": 10_000_000,
    }}

    assert validate_payloads(decision, articles, leaderboard, economics, _requirements()) == []


def test_empty_confirmed_epoch_cannot_pass_content_requirements():
    decision = {
        "epoch": 1052, "status": "confirmed", "attestation_count": 2,
        "required_attestations": 2,
    }
    errors = validate_payloads(decision, [], [], {}, _requirements())

    assert any("missing confirmed article article-1" in error for error in errors)
    assert any("missing rewarded miner miner-1" in error for error in errors)
    assert any("missing confirmed economics for brief brief-1" in error for error in errors)


def test_provisional_or_old_epoch_cannot_pass():
    decision = {
        "epoch": 1051, "status": "provisional", "attestation_count": 1,
        "required_attestations": 2,
    }
    errors = validate_payloads(decision, [], [], {}, {"target_epoch": 1052})

    assert any("status is provisional" in error for error in errors)
    assert any("older than target 1052" in error for error in errors)
    assert any("attestations 1/2" in error for error in errors)
