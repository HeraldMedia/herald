"""USD value of one day of miner emission on this subnet.

Alpha is priced from the subnet's chain pool at the scoring block and TAO from CoinGecko. These
constants are part of the consensus fingerprint (price_source, miner_emission_share,
blocks_per_day), so a change to any of them shows as a fingerprint change.
"""

import math

import httpx

PRICE_SOURCE = "chain_spot_alpha_x_coingecko_tao_usd_v1"
# Share of each block's alpha emission that goes to miners.
MINER_EMISSION_SHARE = 0.41
# One day of blocks (~12 s each); one evaluation epoch has the same length.
BLOCKS_PER_DAY = 7200
# Weights are set on the subnet's first mechanism.
MECHANISM_ID = 0
TAO_USD_URL = "https://api.coingecko.com/api/v3/simple/price?ids=bittensor&vs_currencies=usd"
TAO_USD_ATTEMPTS = 3
TAO_USD_TIMEOUT_SECONDS = 10.0


class PricingError(RuntimeError):
    """A price input is missing, unreadable, non-finite or not positive."""


def _positive(name: str, value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PricingError(f"{name} is not a number: {value!r}") from None
    if not math.isfinite(number) or number <= 0:
        raise PricingError(f"{name} must be finite and positive, got {number!r}")
    return number


def mechanism_ratio(subtensor, netuid: int, block: int) -> float:
    """This mechanism's share of the subnet's emission."""
    split = subtensor.get_mechanism_emission_split(netuid, block=block)
    if split is None:  # evenly split across mechanisms
        count = subtensor.get_mechanism_count(netuid, block=block)
        return 1.0 / _positive("mechanism count", count)
    values = [float(value) for value in split]
    if len(values) <= MECHANISM_ID or not all(math.isfinite(v) and v >= 0 for v in values):
        raise PricingError(f"mechanism emission split {split!r} has no share for mechanism {MECHANISM_ID}")
    total = math.fsum(values)
    if total <= 0:
        raise PricingError(f"mechanism emission split {split!r} sums to zero")
    return values[MECHANISM_ID] / total


def tao_usd(http_get=httpx.get) -> float:
    """TAO/USD from CoinGecko, retried up to TAO_USD_ATTEMPTS times on transport or HTTP errors."""
    last_error = None
    for _attempt in range(TAO_USD_ATTEMPTS):
        try:
            response = http_get(TAO_USD_URL, timeout=TAO_USD_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            last_error = exc
            continue
        try:
            value = data["bittensor"]["usd"]
        except (KeyError, TypeError):
            raise PricingError("TAO/USD response has no bittensor.usd price") from None
        return _positive("tao_usd", value)
    raise PricingError(f"TAO/USD price unavailable after {TAO_USD_ATTEMPTS} attempts: {last_error}")


def daily_miner_usd(subtensor, netuid: int, block: int, http_get=httpx.get) -> dict:
    """Price one day of miner emission. Raises PricingError when any input is unusable.

    daily_miner_alpha = BLOCKS_PER_DAY * alpha_out * MINER_EMISSION_SHARE * ratio
    daily_usd = daily_miner_alpha * alpha_tao * tao_usd
    """
    try:
        alpha_tao = _positive("alpha_tao", subtensor.get_subnet_price(netuid, block=block).tao)
        info = subtensor.subnet(netuid, block=block)
        if info is None:
            raise PricingError(f"subnet {netuid} has no dynamic info at block {block}")
        alpha_out = _positive("alpha_out", info.alpha_out_emission.tao)
        ratio = _positive("mechanism ratio", mechanism_ratio(subtensor, netuid, block))
    except PricingError:
        raise
    except Exception as exc:
        raise PricingError(f"chain price read failed: {exc}") from exc
    daily_miner_alpha = _positive(
        "daily_miner_alpha", BLOCKS_PER_DAY * alpha_out * MINER_EMISSION_SHARE * ratio,
    )
    usd = tao_usd(http_get)
    daily_usd = _positive("daily_usd", daily_miner_alpha * alpha_tao * usd)
    return {
        "alpha_tao": alpha_tao,
        "alpha_out": alpha_out,
        "ratio": ratio,
        "daily_miner_alpha": daily_miner_alpha,
        "tao_usd": usd,
        "daily_usd": daily_usd,
    }
