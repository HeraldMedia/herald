from types import SimpleNamespace

import pytest

from herald.validator.news import forward as fwd


def _response(status, claims=None):
    return SimpleNamespace(
        dendrite=SimpleNamespace(status_code=status, status_message=f"status {status}"),
        claims=claims,
    )


@pytest.mark.asyncio
async def test_collect_claims_retries_only_transient_failures(monkeypatch):
    axon1 = SimpleNamespace(is_serving=True, name="one")
    axon2 = SimpleNamespace(is_serving=True, name="two")
    calls = []

    async def dendrite(*, axons, synapse, deserialize, timeout):
        calls.append(list(axons))
        if len(calls) == 1:
            return [_response(200, ["claim-one"]), _response(408)]
        return [_response(200, ["claim-two"])]

    miner = SimpleNamespace(
        metagraph=SimpleNamespace(axons={1: axon1, 2: axon2}),
        dendrite=dendrite,
    )
    monkeypatch.setattr(fwd, "HERALD_CLAIM_QUERY_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(fwd, "HERALD_CLAIM_QUERY_RETRY_DELAY", 0, raising=False)

    claims = await fwd.collect_claims(miner, [1, 2])

    assert claims == {1: ["claim-one"], 2: ["claim-two"]}
    assert calls == [[axon1, axon2], [axon2]]


@pytest.mark.asyncio
async def test_collect_claims_retries_batch_exceptions(monkeypatch):
    axon = SimpleNamespace(is_serving=True)
    attempts = 0

    async def dendrite(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary axon timeout")
        return [_response(200, ["claim"])]

    miner = SimpleNamespace(
        metagraph=SimpleNamespace(axons={1: axon}),
        dendrite=dendrite,
    )
    monkeypatch.setattr(fwd, "HERALD_CLAIM_QUERY_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(fwd, "HERALD_CLAIM_QUERY_RETRY_DELAY", 0, raising=False)

    assert await fwd.collect_claims(miner, [1]) == {1: ["claim"]}
    assert attempts == 2


@pytest.mark.asyncio
async def test_collect_claims_skips_non_serving_axons(monkeypatch):
    serving = SimpleNamespace(is_serving=True)
    offline = SimpleNamespace(is_serving=False)
    seen = []

    async def dendrite(*, axons, **kwargs):
        seen.extend(axons)
        return [_response(200, [])]

    miner = SimpleNamespace(
        metagraph=SimpleNamespace(axons={1: serving, 2: offline}),
        dendrite=dendrite,
    )

    assert await fwd.collect_claims(miner, [1, 2]) == {1: [], 2: []}
    assert seen == [serving]
