import json

import pytest

from herald.validator.news.registry_signing import generate_keypair, sign


def _registry(tmp_path):
    private_key, public_key = generate_keypair()
    data = {
        "version_id": 1,
        "outlets": [
            {"outlet_id": "direct", "tier": 1, "domains": ["direct.example"]},
            {"outlet_id": "proxy", "tier": 2, "domains": ["proxy.example"], "fetch": "proxy"},
            {"outlet_id": "nyt", "tier": 1, "domains": ["nyt.example"], "fetch": "api:nyt"},
        ],
    }
    data["signature"] = sign(data, private_key)
    path = tmp_path / "outlets.signed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, public_key


def _valid_env(tmp_path):
    path, public_key = _registry(tmp_path)
    return {
        "HERALD_PRODUCTION": "true",
        "HERALD_PRODUCTION_NETUID": "69",
        "SUBTENSOR_NETWORK": "finney",
        "NETUID": "69",
        "HERALD_REGISTRY_PATH": str(path),
        "HERALD_REGISTRY_PUBKEY": public_key,
        "HERALD_REGISTRY_AUTHORITY_HOTKEY": "5Authority",
        "HERALD_REQUIRE_SIGNED_REGISTRY": "true",
        "HERALD_REQUIRE_SIGNED_BRIEFS": "true",
        "HERALD_BRIEFS_PUBKEY": "11" * 32,
        "HERALD_BRIEFS_ENDPOINT": "https://api.herald.network/api/v2/validator/briefs",
        "HERALD_RESULTS_ENDPOINT": "https://api.herald.network",
        "HERALD_RESULTS_TOKEN": "results-token",
        "SCRAPINGBEE_API_KEY": "scrapingbee",
        "HERALD_NYT_API_KEY": "nyt",
        "BRAVE_API_KEY": "brave",
        "HERALD_EXPECTED_CONSENSUS_FP": "expected-fingerprint",
    }


def test_validator_production_environment_accepts_complete_signed_configuration(tmp_path):
    from herald.production import validator_environment_errors

    assert validator_environment_errors(
        _valid_env(tmp_path), actual_consensus="expected-fingerprint",
    ) == []


def test_validator_production_environment_rejects_test_and_simulator_configuration(tmp_path):
    from herald.production import validator_environment_errors

    env = _valid_env(tmp_path)
    env.update({
        "SUBTENSOR_NETWORK": "test",
        "NETUID": "535",
        "HERALD_ALLOW_LOCAL_FETCH": "true",
        "HERALD_SIM_PROVIDER_BASE": "http://localhost:9100",
        "HERALD_RESULTS_ENDPOINT": "http://127.0.0.1:8093",
        "HERALD_ALLOW_OPEN_WRITES": "true",
    })
    errors = validator_environment_errors(env, actual_consensus="different")

    assert any("finney" in error for error in errors)
    assert any("netuid 69" in error for error in errors)
    assert any("local fetch" in error for error in errors)
    assert any("simulator" in error for error in errors)
    assert any("localhost" in error for error in errors)
    assert any("open writes" in error for error in errors)
    assert any("fingerprint" in error for error in errors)


def test_validator_production_environment_requires_strategy_credentials(tmp_path):
    from herald.production import validator_environment_errors

    env = _valid_env(tmp_path)
    for key in ("SCRAPINGBEE_API_KEY", "HERALD_NYT_API_KEY", "BRAVE_API_KEY"):
        env.pop(key)
    errors = validator_environment_errors(env, actual_consensus="expected-fingerprint")

    assert any("SCRAPINGBEE_API_KEY" in error for error in errors)
    assert any("HERALD_NYT_API_KEY" in error for error in errors)
    assert any("search provider" in error for error in errors)


def test_production_validator_check_raises_one_actionable_error(tmp_path):
    from herald.production import ProductionConfigurationError, validate_neuron_environment

    env = _valid_env(tmp_path)
    env["HERALD_REQUIRE_SIGNED_BRIEFS"] = "false"
    with pytest.raises(ProductionConfigurationError, match="signed briefs"):
        validate_neuron_environment(
            "ValidatorNeuron", "finney", 69, env,
            actual_consensus="expected-fingerprint",
        )


def test_production_registry_anchor_check_fails_closed(tmp_path):
    from herald.production import ProductionConfigurationError, validate_registry_anchor

    env = _valid_env(tmp_path)
    with pytest.raises(ProductionConfigurationError, match="no on-chain commitment"):
        validate_registry_anchor(
            object(), 69, env,
            commitments_loader=lambda _subtensor, _netuid: {},
            registry_loader=lambda *_args, **_kwargs: pytest.fail("registry must not load"),
        )


def test_production_registry_anchor_check_verifies_the_live_edition(tmp_path):
    from herald.production import validate_registry_anchor

    env = _valid_env(tmp_path)
    calls = []
    validate_registry_anchor(
        object(), 69, env,
        commitments_loader=lambda _subtensor, _netuid: {
            "5Authority": ("HRLDREG|1|abc|100", 90),
        },
        registry_loader=lambda anchor, **kwargs: calls.append((anchor, kwargs)),
        current_block=120,
    )
    assert calls == [("HRLDREG|1|abc|100", {
        "require_anchor": True, "current_block": 120,
        "network": "finney", "netuid": 69,
    })]
