"""The chain reads and writes Herald performs, on the bittensor SDK.

``Subtensor`` keeps the method names, arguments and return values Herald's code was written
against, so callers do not change and their meaning does not either:

- block numbers, UIDs, counts and limits are ``int``; ``max_weight_limit`` is the u16 limit as a
  fraction of 65535;
- an amount (``get_subnet_price``, ``subnet(...).alpha_out_emission``) is an ``Amount`` holding
  rao, whose ``.tao`` is rao / 1e9 whatever the unit;
- ``get_timestamp`` is a timezone-aware UTC ``datetime``;
- ``get_hotkey_owner`` is None for a hotkey no coldkey owns;
- ``get_mechanism_emission_split`` is each mechanism's share in whole percent, or None when the
  subnet splits emission evenly; ``get_mechanism_count`` is at least 1;
- ``get_timelocked_weight_commits`` lists ``(hotkey, commit_block, ciphertext_hex,
  reveal_round)`` for every commit awaiting reveal, oldest epoch first;
- ``query_map`` yields ``(key, value)`` pairs with ss58 keys and plain decoded values;
- writes return a ``ChainWriteResult`` that unpacks as ``success, message``.

Every read takes ``block=None`` (the chain head) or a block number. A connection uses only the
endpoint it is given: the SDK's public fallback and archive pools are switched off, so a read is
answered by the configured node or fails.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import numpy as np

import bittensor as bt
import bittensor_core

from herald.sdk.log import log

U16_MAX = 65535
RAO_PER_TAO = 10 ** 9
# The chain's mechanism storage index: mechid * GLOBAL_MAX_SUBNET_COUNT + netuid.
GLOBAL_MAX_SUBNET_COUNT = 4096
# SS58 (prefix 42) of the all-zero AccountId: SubtensorModule.Owner's value for an unowned hotkey.
NULL_ACCOUNT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
DEFAULT_PERIOD = 128
MEV_SHIELD_PERIOD = 8
COMMIT_REVEAL_VERSION = 4
BLOCK_TIME_SECONDS = 12.0

FINNEY_ENDPOINT = "wss://entrypoint-finney.opentensor.ai:443"
TEST_ENDPOINT = "wss://test.finney.opentensor.ai:443"
ARCHIVE_ENDPOINT = "wss://archive.chain.opentensor.ai:443"
LATENT_LITE_ENDPOINT = "wss://lite.sub.latent.to:443"

SM = "SubtensorModule"


def _network_map() -> dict:
    return {
        "finney": FINNEY_ENDPOINT,
        "test": TEST_ENDPOINT,
        "archive": ARCHIVE_ENDPOINT,
        "local": os.getenv("BT_SUBTENSOR_CHAIN_ENDPOINT") or "ws://127.0.0.1:9944",
        "latent-lite": LATENT_LITE_ENDPOINT,
    }


def resolve_network(network: Optional[str]) -> tuple:
    """(endpoint URL, network label) for a network name or an endpoint.

    Names map to their public entrypoints. An endpoint is labelled finney, test or archive when it
    is that network's entrypoint, local when it points at 127.0.0.1 or localhost, and unknown
    otherwise; an endpoint without a scheme gets ws://.
    """
    if network is None:
        return None, None
    known = _network_map()
    if network in known:
        label, endpoint = network, known[network]
    else:
        label, endpoint = "unknown", network
        for marker, result in (
            ("entrypoint-finney.opentensor.ai", ("finney", FINNEY_ENDPOINT)),
            ("test.finney.opentensor.ai", ("test", TEST_ENDPOINT)),
            ("archive.chain.opentensor.ai", ("archive", ARCHIVE_ENDPOINT)),
            ("127.0.0.1", ("local", network)),
            ("localhost", ("local", network)),
        ):
            if marker in network:
                label, endpoint = result
                break
    if endpoint is not None and not endpoint.startswith(("ws://", "wss://")):
        endpoint = f"ws://{endpoint}"
    return endpoint, label


def network_from_config(config) -> Optional[str]:
    """The network a parsed config selects.

    The last of these that is present wins: an explicit --subtensor.chain_endpoint, an explicit
    --subtensor.network, the chain_endpoint value, the network value. Since --subtensor.network
    always has a value (finney unless set), --subtensor.chain_endpoint is used only when
    --subtensor.network is given as an empty string.
    """
    subtensor = getattr(config, "subtensor", None)
    if subtensor is None:
        return None
    is_set = getattr(config, "is_set", lambda name: False)
    network = None
    for present, value in (
        (is_set("subtensor.chain_endpoint"), subtensor.get("chain_endpoint")),
        (is_set("subtensor.network"), subtensor.get("network")),
        (subtensor.get("chain_endpoint"), subtensor.get("chain_endpoint")),
        (subtensor.get("network"), subtensor.get("network")),
    ):
        if present:
            network = value
    return network


def _mev_protection_default() -> bool:
    return os.getenv("BT_MEV_PROTECTION", "").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Amount:
    """An on-chain amount in rao. ``tao`` is rao / 1e9 for TAO and alpha alike."""

    rao: int
    netuid: int = 0

    @property
    def tao(self) -> float:
        return self.rao / RAO_PER_TAO

    def __float__(self) -> float:
        return self.tao

    def __int__(self) -> int:
        return self.rao

    def __str__(self) -> str:
        return f"{self.tao:.9f}"


@dataclass
class SubnetDynamics:
    """The parts of SubnetInfoRuntimeApi.get_dynamic_info Herald reads."""

    netuid: int
    alpha_out_emission: Amount
    alpha_in_emission: Amount
    tao_in_emission: Amount
    raw: dict = field(repr=False, default_factory=dict)


@dataclass
class ChainWriteResult:
    """Outcome of a submitted extrinsic; unpacks as ``success, message``."""

    success: bool
    message: str = ""
    data: dict = field(default_factory=dict)
    error: Optional[BaseException] = None
    extrinsic_id: Optional[str] = None

    def __iter__(self):
        yield self.success
        yield self.message

    def __getitem__(self, index):
        return (self.success, self.message)[index]

    def __len__(self):
        return 2

    def __bool__(self):
        return bool(self.success)


def _result(raw, data=None) -> ChainWriteResult:
    return ChainWriteResult(
        success=bool(getattr(raw, "success", False)),
        message=str(getattr(raw, "message", "") or ""),
        data={**(getattr(raw, "data", None) or {}), **(data or {})},
        error=getattr(raw, "error", None),
        extrinsic_id=getattr(raw, "extrinsic_id", None),
    )


def _failure(error: BaseException, raise_error: bool) -> ChainWriteResult:
    if raise_error:
        raise error
    return ChainWriteResult(False, f"{type(error).__name__}: {error}", error=error)


def _missing_storage(error: BaseException) -> bool:
    return type(error).__name__ == "StorageFunctionNotFound"


def normalize_u16(uids, weights) -> tuple:
    """(uids, u16 values): max-upscale ``weights`` to 65535, round, and drop zeros.

    Already max-upscaled u16 values come back unchanged.
    """
    uids = np.asarray(uids, dtype=np.int64).tolist()
    values = np.asarray(weights, dtype=np.float32).tolist()
    if values and min(values) < 0:
        raise ValueError(f"Passed weight is negative cannot exist on chain {values}")
    if uids and min(uids) < 0:
        raise ValueError(f"Passed uid is negative cannot exist on chain {uids}")
    if len(uids) != len(values):
        raise ValueError(
            f"Passed weights and uids must have the same length, got {len(uids)} and {len(values)}"
        )
    if sum(values) == 0:
        return [], []
    top = float(max(values))
    out_uids, out_values = [], []
    for uid, value in zip(uids, values):
        u16 = round(float(value) / top * U16_MAX)
        if u16 != 0:
            out_uids.append(int(uid))
            out_values.append(int(u16))
    return out_uids, out_values


class Subtensor:
    """A blocking chain connection with the reads and writes Herald uses (module docstring)."""

    def __init__(self, network: Optional[str] = None, config=None, *, connect: bool = True,
                 substrate=None):
        if network is None and config is not None:
            network = network_from_config(config)
        self.chain_endpoint, self.network = resolve_network(network or "finney")
        self._substrate = substrate
        self._client = self._new_client()
        if connect:
            self._client.connect()

    def _new_client(self):
        return bt.Subtensor(
            self.chain_endpoint, fallback_endpoints=[], archive_endpoints=[],
            substrate=self._substrate,
        )

    def __repr__(self) -> str:
        return f"Subtensor(network={self.network!r}, chain_endpoint={self.chain_endpoint!r})"

    __str__ = __repr__

    @property
    def client(self):
        """The underlying ``bittensor.Subtensor``."""
        return self._client

    def close(self):
        self._client.close()

    # --- generic -------------------------------------------------------------------------------

    def _view(self, block: Optional[int]):
        return self._client if block is None else self._client.at(int(block))

    def get_current_block(self) -> int:
        return int(self._client.block)

    @property
    def block(self) -> int:
        return self.get_current_block()

    def query(self, module: str, name: str, params: Optional[list] = None,
              block: Optional[int] = None) -> Any:
        return self._view(block).query((module, name), list(params or []))

    def query_map(self, module: str, name: str, params: Optional[list] = None,
                  block: Optional[int] = None) -> list:
        return list(self._view(block).query_map((module, name), list(params or [])))

    def runtime(self, api: str, method: str, params: list, block: Optional[int] = None) -> Any:
        return self._view(block).runtime((api, method), list(params))

    def subnet_exists(self, netuid: int, block: Optional[int] = None) -> bool:
        return bool(self.query(SM, "NetworksAdded", [netuid], block=block))

    def get_hyperparameter(self, param_name: str, netuid: int, block: Optional[int] = None):
        """SubtensorModule.<param_name>[netuid], or None (logged) when the subnet does not exist."""
        if not self.subnet_exists(netuid, block=block):
            log.error(f"subnet {netuid} does not exist")
            return None
        return self.query(SM, param_name, [netuid], block=block)

    # --- registration and keys -----------------------------------------------------------------

    def get_uid_for_hotkey_on_subnet(self, hotkey_ss58: str, netuid: int,
                                     block: Optional[int] = None) -> Optional[int]:
        value = self.query(SM, "Uids", [netuid, hotkey_ss58], block=block)
        return None if value is None else int(value)

    def is_hotkey_registered_on_subnet(self, hotkey_ss58: str, netuid: int,
                                       block: Optional[int] = None) -> bool:
        return self.get_uid_for_hotkey_on_subnet(hotkey_ss58, netuid, block=block) is not None

    def is_hotkey_registered_any(self, hotkey_ss58: str, block: Optional[int] = None) -> bool:
        rows = self.query_map(SM, "IsNetworkMember", [hotkey_ss58], block=block)
        return any(bool(member) for _netuid, member in rows)

    def is_hotkey_registered(self, hotkey_ss58: str, netuid: Optional[int] = None,
                             block: Optional[int] = None) -> bool:
        if netuid is None:
            return self.is_hotkey_registered_any(hotkey_ss58, block=block)
        return self.is_hotkey_registered_on_subnet(hotkey_ss58, netuid, block=block)

    def does_hotkey_exist(self, hotkey_ss58: str, block: Optional[int] = None) -> bool:
        owner = self.query(SM, "Owner", [hotkey_ss58], block=block)
        return bool(owner) and str(owner) != NULL_ACCOUNT

    def get_hotkey_owner(self, hotkey_ss58: str, block: Optional[int] = None) -> Optional[str]:
        """The coldkey in SubtensorModule.Owner for this hotkey, or None when it has none."""
        owner = self.query(SM, "Owner", [hotkey_ss58], block=block)
        if not owner or str(owner) == NULL_ACCOUNT:
            return None
        return str(owner)

    # --- hyperparameters -----------------------------------------------------------------------

    def min_allowed_weights(self, netuid: int, block: Optional[int] = None) -> Optional[int]:
        value = self.get_hyperparameter("MinAllowedWeights", netuid, block=block)
        return None if value is None else int(value)

    def max_weight_limit(self, netuid: int, block: Optional[int] = None) -> Optional[float]:
        """MaxWeightsLimit as a fraction of 65535."""
        value = self.get_hyperparameter("MaxWeightsLimit", netuid, block=block)
        return None if value is None else float(int(value)) / float(U16_MAX)

    def weights_rate_limit(self, netuid: int, block: Optional[int] = None) -> Optional[int]:
        value = self.get_hyperparameter("WeightsSetRateLimit", netuid, block=block)
        return None if value is None else int(value)

    def commit_reveal_enabled(self, netuid: int, block: Optional[int] = None) -> bool:
        value = self.get_hyperparameter("CommitRevealWeightsEnabled", netuid, block=block)
        if value is None:
            raise ValueError(f"commit-reveal setting unavailable: subnet {netuid} does not exist")
        return bool(value)

    def tempo(self, netuid: int, block: Optional[int] = None) -> Optional[int]:
        value = self.get_hyperparameter("Tempo", netuid, block=block)
        return None if value is None else int(value)

    def blocks_since_last_update(self, netuid: int, uid: int,
                                 block: Optional[int] = None) -> Optional[int]:
        block = self.get_current_block() if block is None else int(block)
        values = self.get_hyperparameter("LastUpdate", netuid, block=block)
        if values is None:
            raise ValueError(f"LastUpdate unavailable: subnet {netuid} does not exist")
        return None if len(values) == 0 else block - int(values[uid])

    # --- time, price, emission -----------------------------------------------------------------

    def get_timestamp(self, block: Optional[int] = None) -> datetime:
        """UTC time of `block` from Timestamp.Now."""
        return self._client.timestamp(block)

    def get_subnet_price(self, netuid: int, block: Optional[int] = None) -> Amount:
        """Spot alpha price in TAO per alpha, as an Amount (1 TAO on the root subnet)."""
        if netuid == 0:
            return Amount(RAO_PER_TAO)
        price = self.runtime("SwapRuntimeApi", "current_alpha_price", [netuid], block=block)
        return Amount(int(price))

    def subnet(self, netuid: int, block: Optional[int] = None) -> Optional[SubnetDynamics]:
        """SubnetInfoRuntimeApi.get_dynamic_info, or None when the subnet has none."""
        raw = self.runtime("SubnetInfoRuntimeApi", "get_dynamic_info", [netuid], block=block)
        if not isinstance(raw, dict):
            return None
        return SubnetDynamics(
            netuid=int(raw.get("netuid", netuid)),
            alpha_out_emission=Amount(int(raw["alpha_out_emission"]), netuid),
            alpha_in_emission=Amount(int(raw.get("alpha_in_emission") or 0), netuid),
            tao_in_emission=Amount(int(raw.get("tao_in_emission") or 0)),
            raw=raw,
        )

    def get_mechanism_emission_split(self, netuid: int,
                                     block: Optional[int] = None) -> Optional[list]:
        """Each mechanism's share of the subnet's emission in whole percent, or None."""
        try:
            value = self.query(SM, "MechanismEmissionSplit", [netuid], block=block)
        except ValueError as error:
            if _missing_storage(error):
                return None
            raise
        if value is None:
            return None
        values = [int(v) for v in value]
        total = sum(values)
        return [round(v / total * 100) for v in values]

    def get_mechanism_count(self, netuid: int, block: Optional[int] = None) -> int:
        try:
            value = self.query(SM, "MechanismCountCurrent", [netuid], block=block)
        except ValueError as error:
            if _missing_storage(error):
                return 1
            raise
        return int(value) if value else 1

    # --- weights -------------------------------------------------------------------------------

    def get_timelocked_weight_commits(self, netuid: int, mechid: int = 0,
                                      block: Optional[int] = None) -> list:
        """``(hotkey, commit_block, ciphertext_hex, reveal_round)`` for every pending commit."""
        index = mechid * GLOBAL_MAX_SUBNET_COUNT + netuid
        rows = self.query_map(SM, "TimelockedWeightCommits", [index], block=block)
        commits = []
        for _epoch, entries in sorted(rows, key=lambda row: int(row[0])):
            for hotkey, commit_block, ciphertext, reveal_round in entries or []:
                if isinstance(ciphertext, (bytes, bytearray)):
                    ciphertext = "0x" + bytes(ciphertext).hex()
                commits.append((str(hotkey), int(commit_block), str(ciphertext), int(reveal_round)))
        return commits

    def metagraph(self, netuid: int, lite: bool = True, block: Optional[int] = None):
        from herald.sdk.metagraph import Metagraph

        return Metagraph(netuid=netuid, subtensor=self, lite=lite, sync=True, block=block)

    def _schedule(self, netuid: int, block: int) -> dict:
        def read(name):
            return self.query(SM, name, [netuid], block=block)

        return {
            "last_epoch_block": int(read("LastEpochBlock") or 0),
            "pending_epoch_at": int(read("PendingEpochAt") or 0),
            "subnet_epoch_index": int(read("SubnetEpochIndex") or 0),
            "tempo": int(read("Tempo")),
            "blocks_since_last_step": int(read("BlocksSinceLastStep") or 0),
            "subnet_reveal_period_epochs": int(read("RevealPeriodEpochs")),
        }

    def _forget_cached_nonce(self, address: str) -> None:
        """Make the next write read `address`'s nonce from the chain again.

        The SDK numbers a write from max(the chain's next index, the last nonce it handed out
        + 1) and forgets its own count only on inclusion or a definite rejection. After a
        submission whose outcome it cannot tell (a dropped connection, no answer in time) it
        keeps counting, so if that transaction never reached a node every later write in this
        process is numbered one too high and waits in the pool for a nonce that never comes.
        Dropping the count is safe here: one process signs for this hotkey, one write at a time,
        and the chain's next index already counts a transaction that did reach the pool.
        """
        try:
            self._client._client._substrate.raw.clear_nonce_cache_for_account(address)
        except Exception:
            # No open connection, or the SDK keeps its count elsewhere: a new connection starts
            # with none.
            self._reconnect()

    def _reconnect(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._client = self._new_client()
        try:
            self._client.connect()
        except Exception as error:
            log.warning(f"Could not reconnect to {self.chain_endpoint}: {error}")

    def _submit(self, call, wallet, *, period, wait_for_inclusion, wait_for_finalization,
                mev_protection):
        if mev_protection:
            period = min(int(period), MEV_SHIELD_PERIOD) if period is not None else MEV_SHIELD_PERIOD
        # A shielded write reserves a pair of nonces the SDK must keep track of itself.
        resync = (lambda: None) if mev_protection else (
            lambda: self._forget_cached_nonce(wallet.hotkey.ss58_address))
        try:
            raw = self._client.submit_call(
                call, wallet, signer="hotkey", period=period,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization, shielded=bool(mev_protection),
            )
        except Exception:
            resync()
            raise
        if not getattr(raw, "success", False):
            resync()
        return raw

    def _commit_timelocked_weights(self, wallet, netuid, mechid, uids, weights, *, block_time,
                                   commit_reveal_version, version_key, period, wait_for_inclusion,
                                   wait_for_finalization, mev_protection) -> ChainWriteResult:
        uids, values = normalize_u16(uids, weights)
        current_block = self.get_current_block()
        schedule = self._schedule(netuid, current_block)
        commit, reveal_round = bittensor_core.get_encrypted_commit_v2(
            uids=list(uids),
            weights=list(values),
            version_key=int(version_key),
            last_epoch_block=schedule["last_epoch_block"],
            pending_epoch_at=schedule["pending_epoch_at"],
            subnet_epoch_index=schedule["subnet_epoch_index"],
            tempo=schedule["tempo"],
            blocks_since_last_step=schedule["blocks_since_last_step"],
            current_block=current_block,
            subnet_reveal_period_epochs=schedule["subnet_reveal_period_epochs"],
            block_time=float(block_time),
            hotkey=bytes(wallet.hotkey.public_key),
        )
        call = bt.calls.SubtensorModule.commit_timelocked_mechanism_weights(
            netuid=netuid, mecid=mechid, commit=commit, reveal_round=int(reveal_round),
            commit_reveal_version=int(commit_reveal_version),
        )
        raw = self._submit(call, wallet, period=period, wait_for_inclusion=wait_for_inclusion,
                           wait_for_finalization=wait_for_finalization,
                           mev_protection=mev_protection)
        result = _result(raw, {"commit_for_reveal": commit, "reveal_round": int(reveal_round)})
        (log.debug if result.success else log.error)(result.message)
        return result

    def _set_mechanism_weights(self, wallet, netuid, mechid, uids, weights, *, version_key,
                               period, wait_for_inclusion, wait_for_finalization,
                               mev_protection) -> ChainWriteResult:
        uids, values = normalize_u16(uids, weights)
        call = bt.calls.SubtensorModule.set_mechanism_weights(
            netuid=netuid, mecid=mechid, dests=list(uids), weights=list(values),
            version_key=int(version_key),
        )
        raw = self._submit(call, wallet, period=period, wait_for_inclusion=wait_for_inclusion,
                           wait_for_finalization=wait_for_finalization,
                           mev_protection=mev_protection)
        result = _result(raw)
        (log.debug if result.success else log.error)(result.message)
        return result

    def set_weights(self, wallet, netuid: int, uids, weights, mechid: int = 0,
                    block_time: float = BLOCK_TIME_SECONDS,
                    commit_reveal_version: int = COMMIT_REVEAL_VERSION, max_attempts: int = 5,
                    *, version_key: int, mev_protection: Optional[bool] = None,
                    period: Optional[int] = DEFAULT_PERIOD, raise_error: bool = False,
                    wait_for_inclusion: bool = True, wait_for_finalization: bool = True,
                    wait_for_revealed_execution: bool = True) -> ChainWriteResult:
        """Submit `weights` (u16 values or relative floats) on `uids`, signed by the hotkey.

        With commit-reveal on, the vector is timelock-encrypted to the drand round of its reveal
        epoch and committed (commit-reveal version 4); the chain reveals it. Otherwise it is set
        in plain. The values are max-upscaled to 65535 and zeros dropped; they are not clipped to
        MaxWeightsLimit. Nothing is submitted when the hotkey is not registered, or while the
        blocks since this UID's LastUpdate do not exceed WeightsSetRateLimit. Up to
        `max_attempts` submissions are made until one succeeds.
        ``wait_for_revealed_execution`` is accepted for compatibility and has no effect.
        """
        if mev_protection is None:
            mev_protection = _mev_protection_default()
        if max_attempts < 1:
            return ChainWriteResult(False, "`max_attempts` parameter must be greater than 0.")
        hotkey = wallet.hotkey.ss58_address
        uid = self.get_uid_for_hotkey_on_subnet(hotkey, netuid)
        if uid is None:
            return ChainWriteResult(False, f"Hotkey {hotkey} not registered in subnet {netuid}.")

        def rate_limit_passed() -> bool:
            return int(self.blocks_since_last_update(netuid, uid)) > int(
                self.weights_rate_limit(netuid))

        commit_reveal = self.commit_reveal_enabled(netuid)
        result = ChainWriteResult(False)
        attempt = 0
        while attempt < max_attempts and not result.success and rate_limit_passed():
            log.debug(f"{'Committing' if commit_reveal else 'Setting'} weights for subnet "
                      f"{netuid}. Attempt {attempt + 1} of {max_attempts}.")
            try:
                if commit_reveal:
                    result = self._commit_timelocked_weights(
                        wallet, netuid, mechid, uids, weights, block_time=block_time,
                        commit_reveal_version=commit_reveal_version, version_key=version_key,
                        period=period, wait_for_inclusion=wait_for_inclusion,
                        wait_for_finalization=wait_for_finalization,
                        mev_protection=mev_protection)
                else:
                    result = self._set_mechanism_weights(
                        wallet, netuid, mechid, uids, weights, version_key=version_key,
                        period=period, wait_for_inclusion=wait_for_inclusion,
                        wait_for_finalization=wait_for_finalization,
                        mev_protection=mev_protection)
            except Exception as error:
                return _failure(error, raise_error)
            attempt += 1
        if not result.success:
            log.debug("No one successful attempt made. Perhaps it is too soon to set weights!")
            if raise_error and result.error is not None:
                raise result.error
        return result

    # --- commitments ---------------------------------------------------------------------------

    def set_commitment(self, wallet, netuid: int, data: str, *,
                       mev_protection: Optional[bool] = None,
                       period: Optional[int] = DEFAULT_PERIOD, raise_error: bool = False,
                       wait_for_inclusion: bool = True, wait_for_finalization: bool = True,
                       wait_for_revealed_execution: bool = True) -> ChainWriteResult:
        """Publish `data` as this hotkey's commitment on `netuid` (a single Raw<len> field)."""
        if mev_protection is None:
            mev_protection = _mev_protection_default()
        payload = data.encode()
        info = {"fields": [{f"Raw{len(payload)}": payload}]}
        try:
            call = bt.calls.Commitments.set_commitment(netuid=netuid, info=info)
            raw = self._submit(call, wallet, period=period, wait_for_inclusion=wait_for_inclusion,
                               wait_for_finalization=wait_for_finalization,
                               mev_protection=mev_protection)
        except Exception as error:
            return _failure(error, raise_error)
        result = _result(raw)
        if not result.success and raise_error:
            raise result.error or RuntimeError(result.message or "set_commitment failed")
        return result
