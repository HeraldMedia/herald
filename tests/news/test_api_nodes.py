from types import SimpleNamespace

import pytest

from herald.api import get_query_axons as api_nodes


@pytest.mark.asyncio
async def test_query_api_axons_requires_explicit_metagraph_or_netuid(monkeypatch):
    monkeypatch.setattr(api_nodes.bt, "Dendrite", lambda wallet: object())
    monkeypatch.setattr(api_nodes.bt, "Metagraph", lambda **_kwargs: pytest.fail("unexpected default subnet lookup"))

    with pytest.raises(ValueError, match="metagraph or netuid"):
        await api_nodes.get_query_api_axons(wallet=None)


@pytest.mark.asyncio
async def test_query_api_axons_uses_explicit_netuid(monkeypatch):
    seen = []
    metagraph = SimpleNamespace(axons=["axon"])
    monkeypatch.setattr(api_nodes.bt, "Dendrite", lambda wallet: object())
    monkeypatch.setattr(
        api_nodes.bt, "Metagraph",
        lambda *, netuid, network: seen.append((netuid, network)) or metagraph,
    )
    monkeypatch.setattr(api_nodes, "get_query_api_nodes", lambda *_args, **_kwargs: _uids())

    assert await api_nodes.get_query_api_axons(wallet=None, netuid=69) == ["axon"]
    assert seen == [(69, "finney")]


async def _uids():
    return [0]
