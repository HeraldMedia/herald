"""herald.sdk.metagraph layout, and the container entrypoint's handling of the removed miner."""

import os
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from herald.sdk.metagraph import Metagraph

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _records(n):
    return [{"uid": uid, "hotkey": f"hk{uid}", "coldkey": f"ck{uid}", "active": uid % 2 == 0,
             "validator_permit": uid == 0, "last_update": 100 + uid} for uid in range(n)]


def _subtensor(records, state):
    def runtime(api, method, params, block=None):
        if method == "get_neurons_lite":
            return records
        if isinstance(state, Exception):
            raise state
        return state

    return SimpleNamespace(get_current_block=lambda: 500, runtime=runtime, network="finney")


def test_layout_matches_the_previous_dtypes():
    state = {"alpha_stake": [10 ** 9, 2 * 10 ** 9, 0], "tao_stake": [10 ** 10, 0, 0],
             "total_stake": [5 * 10 ** 9, 2 * 10 ** 9, 0]}
    graph = Metagraph(69, subtensor=_subtensor(_records(3), state))
    assert graph.n.dtype == np.int64 and graph.n.shape == () and int(graph.n) == 3
    assert int(graph.block) == 500
    assert graph.uids.dtype == np.int64 and graph.last_update.dtype == np.int64
    assert graph.validator_permit.dtype == bool
    assert graph.S.dtype == np.float32 and graph.S.tolist() == [5.0, 2.0, 0.0]
    assert graph.alpha_stake.tolist() == [1.0, 2.0, 0.0]
    assert graph.tao_stake.tolist() == pytest.approx([1.8, 0.0, 0.0])
    assert graph.active.tolist() == [1, 0, 1]
    assert graph.hotkeys == ["hk0", "hk1", "hk2"] and graph.coldkeys == ["ck0", "ck1", "ck2"]


def test_a_failed_stake_read_leaves_the_neurons_updated():
    sub = _subtensor(_records(2), RuntimeError("SubnetInfoRuntimeApi unavailable"))
    graph = Metagraph(69, subtensor=sub, block=321)
    assert graph.hotkeys == ["hk0", "hk1"] and int(graph.block) == 321
    assert graph.S.tolist() == []


def test_a_failed_neuron_read_raises():
    def runtime(api, method, params, block=None):
        raise ConnectionError("node down")

    with pytest.raises(ConnectionError):
        Metagraph(69, subtensor=SimpleNamespace(get_current_block=lambda: 1, runtime=runtime))


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_entrypoint_stops_the_removed_miner_before_writing_any_key(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "WALLET_PATH": str(tmp_path / "wallets"),
           "HOTKEY_DATA": "c2VjcmV0"}
    result = subprocess.run(
        ["bash", os.path.join(ROOT, "entrypoint.sh"), "python", "-u", "neurons/miner.py",
         "--wallet.name", "default", "--netuid", "69"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == (
        "[entrypoint] The miner neuron was removed; contributors take part through the Herald "
        "website, so there is nothing to run.")
    assert not (tmp_path / "wallets").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_entrypoint_still_starts_other_commands(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "WALLET_PATH": str(tmp_path / "wallets"),
           "HOTKEY_DATA": "c2VjcmV0", "AXON_EXTERNAL_PORT": "8091"}
    result = subprocess.run(["bash", os.path.join(ROOT, "entrypoint.sh"), "echo", "started"],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip().splitlines()[-1] == "started --axon.external_port 8091"
    assert (tmp_path / "wallets" / "default" / "hotkeys" / "default").read_text() == "secret"
