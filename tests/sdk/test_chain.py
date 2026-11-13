"""herald.sdk.chain against a fake SDK client: return types, units and the weight-setting path."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from herald.base.utils.weight_utils import convert_weights_and_uids_for_emit
from herald.sdk import chain
from herald.sdk.keys import Keypair

NETUID = 69
HOTKEY = Keypair.create_from_uri("//HeraldTestValidator")
OTHER = Keypair.create_from_uri("//HeraldTestOther").ss58_address
COLD = Keypair.create_from_uri("//HeraldTestCold").ss58_address


class FakeView:
    def __init__(self, client, block):
        self.client, self.block = client, block

    def query(self, item, params=None):
        return self.client.query(item, params, block=self.block)

    def query_map(self, item, params=None):
        return self.client.query_map(item, params, block=self.block)

    def runtime(self, method, params):
        return self.client.runtime(method, params, block=self.block)


class _NonceSpy:
    """Stands where the SDK keeps its per-account nonce count (client._client._substrate.raw)."""

    def __init__(self):
        self.cleared = []

    def clear_nonce_cache_for_account(self, address):
        self.cleared.append(address)


class FakeClient:
    """The slice of ``bittensor.Subtensor`` that herald.sdk.chain uses."""

    def __init__(self, storage=None, maps=None, runtime=None, head=1000):
        self.storage = dict(storage or {})
        self.maps = dict(maps or {})
        self.runtimes = dict(runtime or {})
        self.head = head
        self.reads = []
        self.submitted = []
        self.results = []
        self.nonces = _NonceSpy()
        self._client = SimpleNamespace(_substrate=SimpleNamespace(raw=self.nonces))

    @property
    def block(self):
        return self.head

    def at(self, block):
        return FakeView(self, block)

    def _key(self, item, params):
        return (item[0], item[1], tuple(params or []))

    def query(self, item, params=None, *, block=None):
        self.reads.append(("query", item, tuple(params or []), block))
        value = self.storage.get(self._key(item, params), "__missing__")
        if value == "__missing__":
            value = self.storage.get((item[0], item[1]), None)
        if isinstance(value, Exception):
            raise value
        return value

    def query_map(self, item, params=None, *, block=None):
        self.reads.append(("query_map", item, tuple(params or []), block))
        return list(self.maps.get(self._key(item, params), []))

    def runtime(self, method, params, *, block=None):
        self.reads.append(("runtime", method, tuple(params), block))
        return self.runtimes.get((method[0], method[1], tuple(params)))

    def timestamp(self, block=None):
        return datetime.fromtimestamp(int(self.storage[("Timestamp", "Now")]) / 1000, tz=timezone.utc)

    def submit_call(self, call, wallet, **kwargs):
        self.submitted.append((call, wallet, kwargs))
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(success=True, message="Success", data={}, error=None,
                               extrinsic_id="1000-1")

    def close(self):
        self.closed = True


def SM(name, *params):
    return ("SubtensorModule", name, tuple(params))


def _subtensor(client, network="finney"):
    sub = chain.Subtensor(network, connect=False)
    sub._client = client
    return sub


def _registered(extra=None):
    storage = {
        SM("NetworksAdded", NETUID): True,
        SM("Uids", NETUID, HOTKEY.ss58_address): 3,
        SM("CommitRevealWeightsEnabled", NETUID): True,
        SM("WeightsSetRateLimit", NETUID): 100,
        SM("LastUpdate", NETUID): [0, 0, 0, 800],
        SM("LastEpochBlock", NETUID): 990,
        SM("PendingEpochAt", NETUID): 0,
        SM("SubnetEpochIndex", NETUID): 42,
        SM("Tempo", NETUID): 360,
        SM("BlocksSinceLastStep", NETUID): 10,
        SM("RevealPeriodEpochs", NETUID): 1,
    }
    storage.update(extra or {})
    return storage


WALLET = SimpleNamespace(hotkey=HOTKEY)


# --- endpoints ---------------------------------------------------------------------------------

@pytest.mark.parametrize("network, expected", [
    ("finney", ("wss://entrypoint-finney.opentensor.ai:443", "finney")),
    ("test", ("wss://test.finney.opentensor.ai:443", "test")),
    ("archive", ("wss://archive.chain.opentensor.ai:443", "archive")),
    ("local", ("ws://127.0.0.1:9944", "local")),
    ("wss://node.example:9944", ("wss://node.example:9944", "unknown")),
    ("ws://127.0.0.1:9944", ("ws://127.0.0.1:9944", "local")),
    ("node.example:9944", ("ws://node.example:9944", "unknown")),
    ("wss://entrypoint-finney.opentensor.ai:443", ("wss://entrypoint-finney.opentensor.ai:443",
                                                   "finney")),
])
def test_network_names_and_endpoints_resolve_as_before(network, expected, monkeypatch):
    monkeypatch.delenv("BT_SUBTENSOR_CHAIN_ENDPOINT", raising=False)
    assert chain.resolve_network(network) == expected


def test_construction_is_cold_and_pinned_to_the_one_endpoint():
    sub = chain.Subtensor("wss://node.example:443", connect=False)
    assert (sub.network, sub.chain_endpoint) == ("unknown", "wss://node.example:443")
    rpc = sub.client._client._substrate
    assert rpc.fallback_endpoints == [] and rpc.archive_endpoints == []


# --- reads -------------------------------------------------------------------------------------

def test_registration_reads():
    sub = _subtensor(FakeClient(_registered()))
    assert sub.get_uid_for_hotkey_on_subnet(HOTKEY.ss58_address, NETUID) == 3
    assert sub.is_hotkey_registered(netuid=NETUID, hotkey_ss58=HOTKEY.ss58_address) is True
    assert sub.get_uid_for_hotkey_on_subnet(OTHER, NETUID) is None
    assert sub.is_hotkey_registered(netuid=NETUID, hotkey_ss58=OTHER) is False


def test_hotkey_owner_is_none_for_the_null_account_and_otherwise_the_coldkey():
    client = FakeClient({SM("Owner", HOTKEY.ss58_address): COLD,
                         SM("Owner", OTHER): chain.NULL_ACCOUNT})
    sub = _subtensor(client)
    assert sub.get_hotkey_owner(HOTKEY.ss58_address, block=77) == COLD
    assert sub.get_hotkey_owner(OTHER, block=77) is None
    assert sub.get_hotkey_owner("5Missing") is None
    assert ("query", ("SubtensorModule", "Owner"), (HOTKEY.ss58_address,), 77) in client.reads


def test_hyperparameters_keep_their_types_and_units():
    sub = _subtensor(FakeClient({
        SM("NetworksAdded", NETUID): True,
        SM("MinAllowedWeights", NETUID): 1,
        SM("MaxWeightsLimit", NETUID): 6553,
        SM("CommitRevealWeightsEnabled", NETUID): 1,
        SM("WeightsSetRateLimit", NETUID): 100,
    }))
    assert sub.min_allowed_weights(NETUID) == 1 and isinstance(sub.min_allowed_weights(NETUID), int)
    assert sub.max_weight_limit(NETUID) == pytest.approx(6553 / 65535)
    assert sub.commit_reveal_enabled(NETUID) is True
    assert sub.weights_rate_limit(NETUID) == 100


def test_hyperparameters_of_a_missing_subnet_read_as_none(monkeypatch):
    errors = []
    monkeypatch.setattr(chain.log, "error", lambda msg, *a, **k: errors.append(msg))
    sub = _subtensor(FakeClient({SM("NetworksAdded", 7): False}))
    assert sub.min_allowed_weights(7) is None
    assert sub.max_weight_limit(7) is None
    with pytest.raises(ValueError):
        sub.commit_reveal_enabled(7)
    assert errors and all(msg == "subnet 7 does not exist" for msg in errors)


def test_price_and_emission_are_amounts_in_rao_read_as_tao():
    sub = _subtensor(FakeClient(runtime={
        ("SwapRuntimeApi", "current_alpha_price", (NETUID,)): 4_686_407,
        ("SubnetInfoRuntimeApi", "get_dynamic_info", (NETUID,)): {
            "netuid": NETUID, "alpha_out_emission": 1_000_000_000, "alpha_in_emission": 0,
            "tao_in_emission": 0},
    }))
    price = sub.get_subnet_price(NETUID, block=5)
    assert price.rao == 4_686_407 and price.tao == pytest.approx(0.004686407)
    assert sub.get_subnet_price(0).tao == 1.0
    info = sub.subnet(NETUID, block=5)
    assert info.alpha_out_emission.tao == 1.0
    assert sub.subnet(3) is None


def test_mechanism_split_is_whole_percent_or_none():
    client = FakeClient({SM("MechanismEmissionSplit", NETUID): [30000, 35535],
                         SM("MechanismEmissionSplit", 2): None,
                         SM("MechanismCountCurrent", NETUID): 2,
                         SM("MechanismCountCurrent", 2): 0})
    sub = _subtensor(client)
    assert sub.get_mechanism_emission_split(NETUID) == [46, 54]
    assert sub.get_mechanism_emission_split(2) is None
    assert sub.get_mechanism_count(NETUID) == 2
    assert sub.get_mechanism_count(2) == 1


def test_a_storage_item_the_runtime_lacks_reads_as_the_default():
    class StorageFunctionNotFound(ValueError):
        pass

    sub = _subtensor(FakeClient({SM("MechanismEmissionSplit", NETUID): StorageFunctionNotFound("x"),
                                 SM("MechanismCountCurrent", NETUID): StorageFunctionNotFound("x")}))
    assert sub.get_mechanism_emission_split(NETUID) is None
    assert sub.get_mechanism_count(NETUID) == 1


def test_timelocked_commits_cover_every_epoch_oldest_first():
    commits = {
        ("SubtensorModule", "TimelockedWeightCommits", (NETUID,)): [
            (26309, [[OTHER, 990, "0xbb", 12]]),
            (26308, [[HOTKEY.ss58_address, 900, "0xaa", 11], [OTHER, 901, b"\x01", 11]]),
        ],
        ("SubtensorModule", "TimelockedWeightCommits", (4096 + NETUID,)): [(1, [[OTHER, 5, "0x", 1]])],
    }
    sub = _subtensor(FakeClient(maps=commits))
    assert sub.get_timelocked_weight_commits(NETUID) == [
        (HOTKEY.ss58_address, 900, "0xaa", 11), (OTHER, 901, "0x01", 11), (OTHER, 990, "0xbb", 12)]
    assert sub.get_timelocked_weight_commits(NETUID, mechid=1) == [(OTHER, 5, "0x", 1)]


def test_query_map_and_timestamp():
    client = FakeClient({("Timestamp", "Now"): 1_794_536_796_000},
                        maps={("Commitments", "CommitmentOf", (NETUID,)): [("hk", {"block": 1})]})
    sub = _subtensor(client)
    assert sub.query_map(module="Commitments", name="CommitmentOf", params=[NETUID], block=9) == [
        ("hk", {"block": 1})]
    assert client.reads[-1] == ("query_map", ("Commitments", "CommitmentOf"), (NETUID,), 9)
    assert sub.get_timestamp(5) == datetime(2026, 11, 13, 2, 26, 36, tzinfo=timezone.utc)


# --- weights -----------------------------------------------------------------------------------

def test_normalize_u16_keeps_a_u16_vector_and_matches_the_emit_conversion():
    assert chain.normalize_u16([0, 2], [65535, 7282]) == ([0, 2], [65535, 7282])
    rng = np.random.default_rng(7)
    for _ in range(50):
        weights = rng.random(8).astype(np.float32) * rng.integers(0, 2, 8)
        uids = np.arange(8)
        assert chain.normalize_u16(uids, weights) == tuple(
            convert_weights_and_uids_for_emit(uids, weights))
    assert chain.normalize_u16([1, 2], [0.0, 0.0]) == ([], [])
    with pytest.raises(ValueError):
        chain.normalize_u16([1], [-1.0])


def _encrypt_recorder(monkeypatch):
    seen = {}

    def encrypt(**kwargs):
        seen.update(kwargs)
        return b"\xaa\xbb", 777

    monkeypatch.setattr(chain.bittensor_core, "get_encrypted_commit_v2", encrypt)
    return seen


def test_commit_reveal_weights_are_timelocked_with_the_chain_schedule(monkeypatch):
    monkeypatch.delenv("BT_MEV_PROTECTION", raising=False)
    seen = _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    sub = _subtensor(client)

    result = sub.set_weights(wallet=WALLET, netuid=NETUID, uids=[0, 2], weights=[65535, 7282],
                             wait_for_finalization=False, wait_for_inclusion=True,
                             wait_for_revealed_execution=False, version_key=22)

    success, message = result
    assert (success, message) == (True, "Success")
    assert result.data["reveal_round"] == 777
    assert seen == {
        "uids": [0, 2], "weights": [65535, 7282], "version_key": 22, "last_epoch_block": 990,
        "pending_epoch_at": 0, "subnet_epoch_index": 42, "tempo": 360, "blocks_since_last_step": 10,
        "current_block": 1000, "subnet_reveal_period_epochs": 1, "block_time": 12.0,
        "hotkey": bytes(HOTKEY.public_key),
    }
    [(call, wallet, kwargs)] = client.submitted
    assert call.module == "SubtensorModule"
    assert call.function == "commit_timelocked_mechanism_weights"
    assert call.params == {"netuid": NETUID, "mecid": 0, "commit": b"\xaa\xbb", "reveal_round": 777,
                           "commit_reveal_version": 4}
    assert wallet is WALLET
    assert kwargs == {"signer": "hotkey", "period": 128, "wait_for_inclusion": True,
                      "wait_for_finalization": False, "shielded": False}


def test_schedule_is_read_at_the_block_the_commit_is_built_for(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    schedule_reads = [r for r in client.reads if r[1][1] in (
        "LastEpochBlock", "PendingEpochAt", "SubnetEpochIndex", "Tempo", "BlocksSinceLastStep",
        "RevealPeriodEpochs")]
    assert len(schedule_reads) == 6 and {r[3] for r in schedule_reads} == {1000}


def test_plain_weights_when_commit_reveal_is_off(monkeypatch):
    client = FakeClient(_registered({SM("CommitRevealWeightsEnabled", NETUID): False}))
    sub = _subtensor(client)

    assert sub.set_weights(WALLET, NETUID, [0, 2], [0.9, 0.1], version_key=22)[0] is True

    [(call, _wallet, _kwargs)] = client.submitted
    assert call.function == "set_mechanism_weights"
    assert call.params == {"netuid": NETUID, "mecid": 0, "dests": [0, 2], "weights": [65535, 7282],
                           "version_key": 22}


def test_weights_are_not_clipped_to_max_weights_limit(monkeypatch):
    client = FakeClient(_registered({SM("CommitRevealWeightsEnabled", NETUID): False,
                                       SM("MaxWeightsLimit", NETUID): 6553}))
    _subtensor(client).set_weights(WALLET, NETUID, [0, 2], [65535, 7282], version_key=22)
    [(call, _wallet, _kwargs)] = client.submitted
    assert call.params["weights"] == [65535, 7282]


def test_unregistered_hotkey_submits_nothing():
    client = FakeClient(_registered({SM("Uids", NETUID, HOTKEY.ss58_address): None}))
    success, message = _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    assert success is False and "not registered" in message
    assert client.submitted == []


@pytest.mark.parametrize("last_update, submits", [(899, True), (900, False), (950, False)])
def test_rate_limit_needs_more_blocks_than_the_limit(monkeypatch, last_update, submits):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered({SM("LastUpdate", NETUID): [0, 0, 0, last_update]}))
    success, _message = _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    assert success is submits
    assert len(client.submitted) == int(submits)


def test_a_failed_submission_is_retried_up_to_max_attempts(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    failed = SimpleNamespace(success=False, message="Priority is too low", data={}, error=None)
    client.results = [failed, failed]
    sub = _subtensor(client)

    assert sub.set_weights(WALLET, NETUID, [0], [1.0], version_key=22)[0] is True
    assert len(client.submitted) == 3

    client.submitted.clear()
    client.results = [failed] * 5
    assert tuple(sub.set_weights(WALLET, NETUID, [0], [1.0], max_attempts=2, version_key=22)) == (
        False, "Priority is too low")
    assert len(client.submitted) == 2


def test_an_exception_becomes_a_failed_result_unless_asked_to_raise(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("drand unavailable")

    monkeypatch.setattr(chain.bittensor_core, "get_encrypted_commit_v2", broken)
    sub = _subtensor(FakeClient(_registered()))
    success, message = sub.set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    assert success is False and "drand unavailable" in message
    with pytest.raises(RuntimeError):
        sub.set_weights(WALLET, NETUID, [0], [1.0], version_key=22, raise_error=True)


def test_mev_protection_follows_bt_mev_protection(monkeypatch):
    _encrypt_recorder(monkeypatch)
    monkeypatch.setenv("BT_MEV_PROTECTION", "true")
    client = FakeClient(_registered())
    _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    [(_call, _wallet, kwargs)] = client.submitted
    assert kwargs["shielded"] is True and kwargs["period"] == 8


# --- commitments -------------------------------------------------------------------------------

def test_set_commitment_publishes_one_raw_field_signed_by_the_hotkey():
    client = FakeClient()
    result = _subtensor(client).set_commitment(WALLET, NETUID, "HRLDREG|7|abc|5000",
                                               raise_error=True, wait_for_inclusion=True,
                                               wait_for_finalization=True)
    assert result.success is True
    [(call, wallet, kwargs)] = client.submitted
    assert (call.module, call.function) == ("Commitments", "set_commitment")
    assert call.params == {"netuid": NETUID,
                           "info": {"fields": [{"Raw18": b"HRLDREG|7|abc|5000"}]}}
    assert kwargs["signer"] == "hotkey" and kwargs["wait_for_finalization"] is True


def test_a_failed_commitment_raises_when_asked_to():
    client = FakeClient()
    client.results = [SimpleNamespace(success=False, message="TooManyFields", data={},
                                      error=None)]
    with pytest.raises(RuntimeError, match="TooManyFields"):
        _subtensor(client).set_commitment(WALLET, NETUID, "x", raise_error=True)
    client.results = [SimpleNamespace(success=False, message="TooManyFields", data={},
                                      error=None)]
    assert tuple(_subtensor(client).set_commitment(WALLET, NETUID, "x")) == (False, "TooManyFields")



# --- the nonce after a failed write ------------------------------------------------------------

def sub_ok(result):
    return result[0] is True


def _with_nonce_spy(client):
    return client.nonces


def test_a_failed_write_makes_the_next_one_read_the_nonce_from_the_chain(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    spy = _with_nonce_spy(client)
    failed = SimpleNamespace(success=False, message="connection lost", data={}, error=None)
    client.results = [failed, failed]

    assert sub_ok(_subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22))
    assert spy.cleared == [HOTKEY.ss58_address] * 2  # once per failed attempt, none for the success


def test_a_write_that_raises_also_drops_the_cached_nonce(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    spy = _with_nonce_spy(client)

    def lost(call, wallet, **kwargs):
        raise ConnectionError("socket closed before the node answered")

    client.submit_call = lost
    success, message = _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22)
    assert success is False and "socket closed" in message
    assert spy.cleared == [HOTKEY.ss58_address]


def test_a_successful_write_keeps_the_sdk_nonce_count(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    spy = _with_nonce_spy(client)
    assert sub_ok(_subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22))
    assert spy.cleared == []


def test_a_failed_shielded_write_leaves_the_sdk_nonce_pair_alone(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    spy = _with_nonce_spy(client)
    client.results = [SimpleNamespace(success=False, message="no", data={}, error=None)] * 5
    result = _subtensor(client).set_weights(WALLET, NETUID, [0], [1.0], version_key=22,
                                            mev_protection=True)
    assert result[0] is False and spy.cleared == []


def test_a_failed_commitment_drops_the_cached_nonce_too():
    client = FakeClient(_registered())
    spy = _with_nonce_spy(client)
    client.results = [SimpleNamespace(success=False, message="no", data={}, error=None)]
    assert _subtensor(client).set_commitment(WALLET, NETUID, "x")[0] is False
    assert spy.cleared == [HOTKEY.ss58_address]


def test_without_the_sdk_nonce_count_a_failed_write_reconnects(monkeypatch):
    _encrypt_recorder(monkeypatch)
    client = FakeClient(_registered())
    del client._client  # the SDK's internals are not where they were
    client.results = [SimpleNamespace(success=False, message="no", data={}, error=None)]
    fresh = FakeClient(_registered())
    fresh.connect = lambda: None
    sub = _subtensor(client)
    monkeypatch.setattr(sub, "_new_client", lambda: fresh)

    assert sub_ok(sub.set_weights(WALLET, NETUID, [0], [1.0], version_key=22))
    assert client.closed is True and sub.client is fresh and len(fresh.submitted) == 1


def test_the_sdk_keeps_counting_after_an_unknown_outcome_until_its_count_is_cleared():
    """The bittensor behaviour the two tests above answer, and where its count lives."""
    import asyncio

    from bittensor._substrate import RpcSubstrate
    from bittensor._transport.extrinsics import NonceCache
    from bittensor._transport import interface

    class Session:
        async def request(self, method, params):
            assert method == "account_nextIndex"
            return 7  # the chain never saw the first transaction

    cache = NonceCache(Session())
    address = HOTKEY.ss58_address
    assert asyncio.run(cache.reserve(address)) == 7
    assert asyncio.run(cache.reserve(address)) == 8  # one too high: it would wait for nonce 7
    cache.clear(address)
    assert asyncio.run(cache.reserve(address)) == 7

    assert isinstance(getattr(RpcSubstrate, "raw", None), property)
    assert any(hasattr(member, "clear_nonce_cache_for_account")
               for member in vars(interface).values() if isinstance(member, type))
