"""Dispute settlement semantics (the validator Pass-1 rule), tested via the real
settle_persistence over the real vesting/slash/dispute ledgers."""

from herald.validator.news.disputes import DisputeLedger, settle_persistence
from herald.validator.news.slashing import SlashLedger
from herald.validator.news.vesting import VestingLedger

DEAD_CONFIRM, COOLDOWN, WINDOW, FRACTION = 2, 7, 4, 0.5


def _setup(total_usd=500.0, vest=30):
    ves = VestingLedger(vest)
    ves.start("art1", uid=1, total_usd=total_usd, hotkey="hkMiner", brief_id="b1",
              commit_epoch=0, start_epoch=0)
    return ves, SlashLedger(), DisputeLedger()


def _settle(ves, sl, dis, status, epoch):
    return settle_persistence(
        "art1", ves.entry("art1"), status, epoch,
        vesting=ves, slash=sl, disputes=dis,
        dead_confirm=DEAD_CONFIRM, cooldown=COOLDOWN, window=WINDOW,
        reward_fraction=FRACTION, uid_by_hotkey={"hkDisp": 7},
    )


def test_dispute_upheld_rewards_disputer_and_slashes_miner():
    ves, sl, dis = _setup()
    dis.open("art1", "hkDisp", 0)
    _settle(ves, sl, dis, "alive", 0)                 # 1 installment released; remaining 29
    _settle(ves, sl, dis, "dead", 1)                  # dead_streak 1, not yet confirmed
    inst, rewards = _settle(ves, sl, dis, "dead", 2)  # confirmed -> clawback + slash + reward
    assert ves.status("art1") == "CLAWBACK"
    assert sl.is_slashed("hkMiner", 2)
    assert dis.to_dict()["art1"]["status"] == "upheld"
    assert rewards == {7: (500.0 / 30) * 29 * FRACTION}  # half the forfeited USD
    assert inst == 0.0


def test_dispute_rejected_slashes_disputer_not_miner():
    ves, sl, dis = _setup()
    dis.open("art1", "hkDisp", 0)
    for e in range(4):                                # alive within the window: miner paid, no reject
        _settle(ves, sl, dis, "alive", e)
    assert not sl.is_slashed("hkDisp", 3)
    _settle(ves, sl, dis, "alive", 4)                 # window reached -> reject + grief slash
    assert dis.to_dict()["art1"]["status"] == "rejected"
    assert sl.is_slashed("hkDisp", 4)
    assert not sl.is_slashed("hkMiner", 4)
    assert ves.status("art1") == "VESTING"            # miner untouched


def test_undisputed_dead_slashes_miner_without_reward():
    ves, sl, dis = _setup()                           # no dispute opened
    _settle(ves, sl, dis, "dead", 1)
    _, rewards = _settle(ves, sl, dis, "dead", 2)
    assert ves.status("art1") == "CLAWBACK"
    assert sl.is_slashed("hkMiner", 2)
    assert rewards == {}


def test_hold_keeps_dispute_open_and_withholds_pay():
    ves, sl, dis = _setup()
    dis.open("art1", "hkDisp", 0)
    inst, rewards = _settle(ves, sl, dis, "hold", 1)
    assert inst == 0.0 and rewards == {}
    assert ves.status("art1") == "VESTING"
    assert dis.is_disputed("art1")                    # unresolved -> stays open
