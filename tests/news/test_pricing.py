import math
from types import SimpleNamespace

import pytest

from herald.validator.news import pricing
from herald.validator.news.pricing import PricingError, daily_miner_usd

NETUID = 69
BLOCK = 9_000_000


class FakeSubtensor:
    """Answers the chain reads pricing makes; nothing here opens a connection."""

    def __init__(self, *, price=0.004, alpha_out=0.5, split=(40, 60), count=2, dynamic_info=True):
        self.price = price
        self.alpha_out = alpha_out
        self.split = split
        self.count = count
        self.dynamic_info = dynamic_info
        self.blocks = []

    def get_subnet_price(self, netuid, block=None):
        self.blocks.append(("price", netuid, block))
        return SimpleNamespace(tao=self.price)

    def subnet(self, netuid, block=None):
        self.blocks.append(("subnet", netuid, block))
        if not self.dynamic_info:
            return None
        return SimpleNamespace(alpha_out_emission=SimpleNamespace(tao=self.alpha_out))

    def get_mechanism_emission_split(self, netuid, block=None):
        self.blocks.append(("split", netuid, block))
        return None if self.split is None else list(self.split)

    def get_mechanism_count(self, netuid, block=None):
        self.blocks.append(("count", netuid, block))
        return self.count


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.payload


class FakeHttp:
    """Returns the queued responses in order, repeating the last one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append((url, timeout))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def tao_at(usd):
    return FakeHttp(FakeResponse(payload={"bittensor": {"usd": usd}}))


def test_constants_are_fixed_in_code():
    assert pricing.MINER_EMISSION_SHARE == 0.41
    assert pricing.BLOCKS_PER_DAY == 7200
    assert pricing.MECHANISM_ID == 0
    assert pricing.TAO_USD_URL == (
        "https://api.coingecko.com/api/v3/simple/price?ids=bittensor&vs_currencies=usd"
    )
    assert pricing.PRICE_SOURCE == "chain_spot_alpha_x_coingecko_tao_usd_v1"


def test_daily_miner_usd_for_fixed_inputs():
    subtensor = FakeSubtensor(price=0.004, alpha_out=0.5, split=(40, 60))
    http = tao_at(300.0)

    result = daily_miner_usd(subtensor, NETUID, BLOCK, http_get=http)

    assert result["alpha_tao"] == pytest.approx(0.004)
    assert result["alpha_out"] == pytest.approx(0.5)
    assert result["ratio"] == pytest.approx(0.4)
    assert result["daily_miner_alpha"] == pytest.approx(7200 * 0.5 * 0.41 * 0.4)  # 590.4
    assert result["tao_usd"] == pytest.approx(300.0)
    assert result["daily_usd"] == pytest.approx(590.4 * 0.004 * 300.0)  # 708.48
    assert all(block == BLOCK and netuid == NETUID for _name, netuid, block in subtensor.blocks)
    assert http.calls == [(pricing.TAO_USD_URL, 10.0)]


def test_split_sets_this_mechanisms_share():
    result = daily_miner_usd(FakeSubtensor(split=(40, 60)), NETUID, BLOCK, http_get=tao_at(1.0))
    assert result["ratio"] == pytest.approx(0.4)


def test_evenly_split_emission_uses_the_mechanism_count():
    result = daily_miner_usd(FakeSubtensor(split=None, count=2), NETUID, BLOCK, http_get=tao_at(1.0))
    assert result["ratio"] == pytest.approx(0.5)


@pytest.mark.parametrize("split", [(), (0, 0), (0, 100)])
def test_unusable_split_is_a_pricing_error(split):
    with pytest.raises(PricingError):
        daily_miner_usd(FakeSubtensor(split=split), NETUID, BLOCK, http_get=tao_at(1.0))


@pytest.mark.parametrize("count", [0, None])
def test_unusable_mechanism_count_is_a_pricing_error(count):
    with pytest.raises(PricingError):
        daily_miner_usd(FakeSubtensor(split=None, count=count), NETUID, BLOCK, http_get=tao_at(1.0))


def test_tao_usd_server_errors_fail_after_three_attempts():
    http = FakeHttp(FakeResponse(status=503))
    with pytest.raises(PricingError, match="after 3 attempts"):
        daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert len(http.calls) == 3


def test_a_transient_tao_usd_failure_is_retried():
    http = FakeHttp(ConnectionError("reset"), FakeResponse(status=502),
                    FakeResponse(payload={"bittensor": {"usd": 250.0}}))
    result = daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert result["tao_usd"] == 250.0 and len(http.calls) == 3


@pytest.mark.parametrize("payload", [
    {"bittensor": {"usd": 0}},
    {"bittensor": {"usd": -1.0}},
    {"bittensor": {"usd": math.nan}},
    {"bittensor": {"usd": "n/a"}},
    {"bittensor": {}},
    {},
    None,
])
def test_unusable_tao_usd_price_is_a_pricing_error(payload):
    with pytest.raises(PricingError):
        daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=FakeHttp(FakeResponse(payload=payload)))


@pytest.mark.parametrize("field, value", [
    ("price", 0.0), ("price", math.nan), ("alpha_out", 0.0), ("alpha_out", math.inf),
])
def test_unusable_chain_values_are_a_pricing_error(field, value):
    with pytest.raises(PricingError):
        daily_miner_usd(FakeSubtensor(**{field: value}), NETUID, BLOCK, http_get=tao_at(1.0))


def test_missing_subnet_info_is_a_pricing_error():
    http = tao_at(1.0)
    with pytest.raises(PricingError, match="no dynamic info"):
        daily_miner_usd(FakeSubtensor(dynamic_info=False), NETUID, BLOCK, http_get=http)
    assert http.calls == []


def test_a_chain_read_error_is_a_pricing_error():
    subtensor = FakeSubtensor()

    def unreachable(netuid, block=None):
        raise ConnectionError("node unreachable")

    subtensor.get_subnet_price = unreachable
    with pytest.raises(PricingError, match="node unreachable"):
        daily_miner_usd(subtensor, NETUID, BLOCK, http_get=tao_at(1.0))
