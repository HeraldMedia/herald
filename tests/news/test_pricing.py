import math
from types import SimpleNamespace

import pytest

from herald.validator.news import pricing
from herald.validator.news.pricing import PricingError, daily_miner_usd

NETUID = 69
BLOCK = 9_000_000
COIN_RECORD_URL = pricing.COINGECKO_API + pricing.TAO_USD_PATHS[1]


@pytest.fixture(autouse=True)
def keyless_and_no_waits(monkeypatch):
    """Keyless CoinGecko access unless a test sets a key, and backoff pauses recorded, not slept."""
    monkeypatch.delenv("HERALD_COINGECKO_API_KEY", raising=False)
    monkeypatch.delenv("HERALD_COINGECKO_API_PLAN", raising=False)
    waits = []
    monkeypatch.setattr(pricing.time, "sleep", waits.append)
    return waits


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
        self.headers = []

    def __call__(self, url, timeout=None, headers=None):
        self.calls.append((url, timeout))
        self.headers.append(dict(headers or {}))
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
    assert COIN_RECORD_URL == (
        "https://api.coingecko.com/api/v3/coins/bittensor?localization=false&tickers=false"
        "&market_data=true&community_data=false&developer_data=false&sparkline=false"
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


def test_tao_usd_server_errors_fail_after_three_rounds_of_both_routes(keyless_and_no_waits):
    http = FakeHttp(FakeResponse(status=503))
    with pytest.raises(PricingError, match="after 3 attempts"):
        daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert [url for url, _timeout in http.calls] == [pricing.TAO_USD_URL, COIN_RECORD_URL] * 3
    assert keyless_and_no_waits == [10.0, 30.0]


def test_a_refused_simple_price_falls_back_to_the_coin_record(keyless_and_no_waits):
    http = FakeHttp(FakeResponse(status=403),
                    FakeResponse(payload={"market_data": {"current_price": {"usd": 310.77, "eur": 1.0}}}))
    result = daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert result["tao_usd"] == pytest.approx(310.77)
    assert [url for url, _timeout in http.calls] == [pricing.TAO_USD_URL, COIN_RECORD_URL]
    assert keyless_and_no_waits == []


def test_keyless_requests_carry_no_key():
    http = tao_at(300.0)
    daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert http.headers == [{"accept": "application/json"}]


def test_a_demo_key_is_sent_to_the_public_api_as_a_header(monkeypatch):
    monkeypatch.setenv("HERALD_COINGECKO_API_KEY", " CG-demo-key ")
    http = FakeHttp(FakeResponse(status=429), FakeResponse(status=429),
                    FakeResponse(payload={"bittensor": {"usd": 300.0}}))
    daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert all(url.startswith("https://api.coingecko.com/api/v3/") for url, _timeout in http.calls)
    assert all(headers == {"accept": "application/json", "x-cg-demo-api-key": "CG-demo-key"}
               for headers in http.headers)


def test_a_pro_key_goes_to_the_pro_api(monkeypatch):
    monkeypatch.setenv("HERALD_COINGECKO_API_KEY", "CG-pro-key")
    monkeypatch.setenv("HERALD_COINGECKO_API_PLAN", "Pro")
    http = tao_at(300.0)
    daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=http)
    assert http.calls == [(pricing.COINGECKO_PRO_API + pricing.TAO_USD_PATHS[0], 10.0)]
    assert http.headers == [{"accept": "application/json", "x-cg-pro-api-key": "CG-pro-key"}]


def test_the_key_never_appears_in_a_pricing_error(monkeypatch):
    monkeypatch.setenv("HERALD_COINGECKO_API_KEY", "CG-secret-key")
    with pytest.raises(PricingError) as failure:
        daily_miner_usd(FakeSubtensor(), NETUID, BLOCK, http_get=FakeHttp(FakeResponse(status=403)))
    assert "CG-secret-key" not in str(failure.value)


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
