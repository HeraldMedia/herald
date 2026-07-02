"""
Herald end-to-end simulation: operator -> miner -> validator, in one process.

Only the THREE true external seams are faked (chain, web, search-index). Everything
else is the real code: the operator's brief board store + Ed25519 feed signing, the
miner's ClaimStore commit (real pre_hash) + claim snapshot + neuron serve path, and
the validator's forward() loop (oracle -> attribution -> vesting -> emission -> weights).
"""

import os
import tempfile

# ---- consensus-critical env MUST be set before importing herald config ----
os.environ.update(
    HERALD_VEST_EPOCHS="3",          # short vest so the lifecycle fits in a few epochs
    HERALD_VEST_EPOCH_LEN="7200",    # ~1 day
    HERALD_EPOCH_LEN="360",
    HERALD_EPOCH_LAG="10",
    HERALD_DEAD_CONFIRM_EPOCHS="1",  # one confirmed-dead epoch slashes (keeps the demo short)
    HERALD_TOTAL_DAILY_USD="1000",   # show the burn of the unclaimed remainder
    HERALD_SNAPSHOT_ANCHOR="0.5",
    HERALD_BRIEFS_MAX_AGE="900",
)

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bittensor as bt

from herald.miner.claim_store import ClaimStore
from herald.miner.commit import submit_commitment
from herald.protocol import ClaimRecord
from herald.services.store import BriefStore
from herald.validator.news import fetch as fetchmod
from herald.validator.news import forward as fwd
from herald.validator.news import search as searchmod
from herald.validator.news.oracle import evaluate_article
from herald.validator.news.registry import load_registry
from herald.validator.news.registry_signing import generate_keypair
from herald.validator.news.signed_briefs import sign_briefs
from herald.validator.news.state import HeraldState
from herald.validator.utils import briefs as briefsmod
from herald.validator.utils.consensus import consensus_fingerprint

bt.logging.off()  # keep the narrative clean; we print our own report

GENESIS = datetime(2026, 6, 1, tzinfo=timezone.utc)
COMMIT_BLOCK = 216_000            # -> 2026-07-01T00:00Z
VEST_LEN = 7200


def blk_time(block):
    return GENESIS + timedelta(seconds=12 * block)


def hr(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


# ─────────────────────────────────────────────────────────────────────────────
# Fake CHAIN: remembers each hotkey's commitment + the block it landed at, and maps
# block -> wall-clock (so the freshness gate can compare commit time vs publish time).
# ─────────────────────────────────────────────────────────────────────────────
class FakeChain:
    def __init__(self):
        self.block = COMMIT_BLOCK
        self._commits = {}  # hotkey -> (onchain_value, block)

    def commit(self, wallet, netuid, value):
        self._commits[wallet.hotkey.ss58_address] = (value, self.block)

    def get_current_block(self):
        return self.block

    def get_timestamp(self, block):
        return blk_time(block)

    def with_block(self):
        return dict(self._commits)


chain = FakeChain()


def wallet(hotkey):
    return SimpleNamespace(hotkey=SimpleNamespace(ss58_address=hotkey))


# ─────────────────────────────────────────────────────────────────────────────
# Fake WEB: url -> (status, html). Each validator can be handed a different page
# variant to prove snapshot anchoring collapses per-validator fetch variance.
# ─────────────────────────────────────────────────────────────────────────────
def page(body, published="2026-07-01T06:00:00+00:00", author="Jane Reporter"):
    return (
        f'<html><head><meta property="article:published_time" content="{published}" />'
        f'<meta name="author" content="{author}" /></head>'
        f"<body><article><p>{body}</p></article></body></html>"
    )


DRAFT_A = ("Herald opened its public pilot this week, saying earned media coverage "
           "should be provable rather than promised.")
BODY_A = (DRAFT_A + " The project, built on Bittensor, pays contributors only for "
          "articles that pass an automated verification oracle run by independent "
          "validators. Operators submit a published link and the placement is confirmed "
          "before any reward is released. Herald frames the approach as real "
          "accountability for the public relations industry.")
BODY_B = ("A new Bittensor subnet called Herald wants to verify earned media placements "
          "on chain, paying public relations operators in emissions only after an oracle "
          "confirms the article is genuine news rather than an advertisement. The team says "
          "each placement is checked for a real byline, a live public URL, and inclusion in "
          "mainstream search results before any reward is released to the miner who produced it. "
          "Backers argue the design finally makes public relations outcomes measurable and auditable.")
BODY_C = ("A DeFi protocol disclosed a governance overhaul today, with the team outlining "
          "new vault mechanics and a revised emissions schedule for liquidity providers "
          "across several chains. The proposal, published for community review, adjusts fee "
          "tiers and introduces a longer lock period intended to reward committed depositors. "
          "Independent analysts said the changes could reshape how yield is distributed across "
          "the protocol's largest pools over the coming quarter.")

URL_A = "https://www.nytimes.com/2026/07/01/tech/herald-pilot.html"
URL_B = "https://techcrunch.com/2026/07/01/herald-launch/"
URL_C = "https://www.coindesk.com/2026/07/01/defi-herald/"

# Validator-1 sees clean pages. Two V2 variants of the nytimes page model per-validator
# fetch variance:
#   • DRAFTLESS: the committed draft sentence is paraphrased away but the topic survives —
#     the class of variance snapshot anchoring FULLY covers (attribution + emission agree).
#   • TOPICLESS: every brief keyword scrubbed out — exposes the residual that the per-epoch
#     persistence re-check runs on the live fetch, not the snapshot.
WEB_V1 = {URL_A: (200, page(BODY_A)), URL_B: (200, page(BODY_B)), URL_C: (200, page(BODY_C))}
_draftless_A = BODY_A.replace(
    DRAFT_A, "The company launched its public pilot this week, framing verifiable coverage as its promise.")
_topicless_A = BODY_A.replace("Herald", "The startup").replace("Bittensor", "a decentralized network")
WEB_V2_DRAFTLESS = {**WEB_V1, URL_A: (200, page(_draftless_A))}
WEB_V2_TOPICLESS = {**WEB_V1, URL_A: (200, page(_topicless_A))}

CURRENT_WEB = {"pages": WEB_V1}


def fake_http_get(url):
    from herald.validator.news.url import canonicalize
    pages = CURRENT_WEB["pages"]
    for u, (status, html) in pages.items():
        if canonicalize(u) == canonicalize(url):
            return (status, u, html.encode("utf-8"))
    return (404, url, b"")


# search index: every article URL is indexed (canonical form)
from herald.validator.news.url import article_id, canonicalize  # noqa: E402
INDEXED = {canonicalize(u) for u in (URL_A, URL_B, URL_C)}

fetchmod._http_get = fake_http_get
fetchmod.is_safe_fetch_url = lambda url: True          # offline: skip DNS/SSRF resolution
searchmod._serpapi_search = lambda q, n: [q] if q in INDEXED else []
fwd.get_all_uids = lambda self: [0, 1, 2, 3]           # burn + 3 miners
fwd.get_commitments_with_block = lambda subtensor, netuid: chain.with_block()
fwd.time.sleep = lambda *_: None


def set_web(pages):
    CURRENT_WEB["pages"] = pages
    fetchmod._cache.clear()
    searchmod._cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Miner serve path — copied verbatim from neurons/miner.py forward().
# ─────────────────────────────────────────────────────────────────────────────
def serve(store_path):
    records = ClaimStore(store_path).active_claims()
    return [
        ClaimRecord(
            brief_id=r["brief_id"], target_outlet_id=r["target_outlet_id"],
            article_url=r["article_url"], claimer_hotkey=r["claimer_hotkey"],
            nonce=r["nonce"], bond_atto=r["bond_atto"], version_id=r["version_id"],
            pre_hash=r.get("pre_hash") or None,
            evidence_text=(r.get("evidence") or {}).get("text"),
            evidence_author=(r.get("evidence") or {}).get("author"),
            evidence_window=(r.get("evidence") or {}).get("window"),
            snapshot_text=r.get("snapshot_text") or None,
        )
        for r in records
    ]


def make_validator(state, stores_by_uid):
    async def dendrite(axons, synapse, deserialize, timeout):
        return [SimpleNamespace(claims=serve(stores_by_uid[a]) if a in stores_by_uid else [])
                for a in axons]

    captured = {}
    v = SimpleNamespace(
        step=0, config=SimpleNamespace(netuid=69),
        subtensor=chain,
        metagraph=SimpleNamespace(
            hotkeys={0: "burn", 1: "hkA", 2: "hkB", 3: "hkC"},
            axons={0: 0, 1: 1, 2: 2, 3: 3},
            alpha_stake={0: 0.0, 1: 5000.0, 2: 5000.0, 3: 5000.0},
        ),
        dendrite=dendrite,
        update_scores=lambda w, uids: captured.update(w=dict(zip(uids, w))),
    )
    v.herald_state = state
    return v, captured


# =============================================================================
hr("PHASE 0 — Consensus fingerprint (every validator must match)")
fp = consensus_fingerprint()
print(f"consensus fingerprint (V1) : {fp}")
print(f"consensus fingerprint (V2) : {consensus_fingerprint()}")
assert consensus_fingerprint() == fp
print("→ identical: both validators price every claim by the same rulebook.")

# =============================================================================
hr("PHASE 1 — OPERATOR: create + fund briefs, sign the validator feed")
tmp = tempfile.mkdtemp(prefix="herald_e2e_")
brief_store = BriefStore(os.path.join(tmp, "briefs.json"))

brief_store.create({"id": "b_news", "kind": "standing", "title": "Herald launch coverage",
                    "keywords": ["herald", "bittensor"]})
brief_store.fund("b_news")  # standing: pays from emissions, no reward_pool

brief_store.create({"id": "b_defi", "kind": "client", "title": "DeFi protocol relaunch",
                    "keywords": ["defi"], "start_date": "2026-06-01", "end_date": "2026-12-31"})
brief_store.fund("b_defi", reward_pool=300.0,
                 payment_ref={"tx": "0xDEADBEEF", "amount": 300, "currency": "USD"})

sk, pk = generate_keypair()
os.environ["HERALD_BRIEFS_PUBKEY"] = pk   # verify_briefs reads this at call time
import time as _time
feed = sign_briefs({"items": brief_store.open_briefs(), "signed_at": _time.time()}, sk)

# The validator's real get_briefs() fetches over HTTP + verifies the operator signature.
briefsmod.requests = SimpleNamespace(
    get=lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: feed),
    exceptions=briefsmod.requests.exceptions,
)
registry = load_registry()
print(f"operator briefs   : {[b['id'] + '(' + b['kind'] + ')' for b in brief_store.open_briefs()]}")
print(f"b_defi reward_pool: ${brief_store.get('b_defi')['reward_pool']:.0f} (prepaid, drawn down per epoch)")
print(f"feed signed with  : operator ed25519 key {pk[:16]}…   signature {feed['signature'][:16]}…")
print(f"outlet registry   : v{registry.version_id}, {len(registry.outlets)} outlets "
      f"(nytimes=t1, techcrunch=t2, coindesk=t2)")

# operator-signature guarantee: a tampered feed fails closed
tampered = {**feed, "items": [{**feed["items"][0], "reward_pool": 999999}]}
briefsmod.requests.get = lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: tampered)
try:
    briefsmod.get_briefs(now=blk_time(COMMIT_BLOCK))
    raise SystemExit("SECURITY FAIL: tampered feed accepted")
except ValueError:
    print("tamper check     : editing reward_pool after signing → feed REJECTED ✓")
briefsmod.requests.get = lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: feed)

# =============================================================================
hr("PHASE 2 — MINER: commit BEFORE publishing (real pre_hash on chain)")
chain.block = COMMIT_BLOCK
store_A, store_B, store_C = (os.path.join(tmp, f"claims_{x}.json") for x in "ABC")

oc_A = submit_commitment(chain, wallet("hkA"), 69, ClaimStore(store_A),
                         brief_id="b_news", target_outlet_id="nytimes",
                         bond_atto=10**21, version_id=1, evidence={"text": DRAFT_A})
oc_B = submit_commitment(chain, wallet("hkB"), 69, ClaimStore(store_B),
                         brief_id="b_news", target_outlet_id="techcrunch",
                         bond_atto=10**21, version_id=1, evidence=None)
oc_C = submit_commitment(chain, wallet("hkC"), 69, ClaimStore(store_C),
                         brief_id="b_defi", target_outlet_id="coindesk",
                         bond_atto=10**21, version_id=1, evidence=None)
print(f"hkA → nytimes  (tier1, b_news)  TEXT-PROOF  commit={oc_A[:22]}…  block {chain.block}")
print(f"hkB → techcrunch(tier2, b_news)  bare        commit={oc_B[:22]}…  block {chain.block}")
print(f"hkC → coindesk (tier2, b_defi)  bare        commit={oc_C[:22]}…  block {chain.block}")
print(f"commit time = {blk_time(COMMIT_BLOCK):%Y-%m-%d %H:%M} UTC  (articles must post AFTER this)")

# =============================================================================
hr("PHASE 3 — MINER: publish, then claim with a page snapshot")
def snapshot_of(html):
    return fetchmod._extract_text(html)

ClaimStore(store_A).set_article_url(oc_A, URL_A, snapshot_text=snapshot_of(WEB_V1[URL_A][1]))
ClaimStore(store_B).set_article_url(oc_B, URL_B, snapshot_text=snapshot_of(WEB_V1[URL_B][1]))
ClaimStore(store_C).set_article_url(oc_C, URL_C, snapshot_text=snapshot_of(WEB_V1[URL_C][1]))
print(f"hkA claim: {URL_A}\n           + snapshot ({len(snapshot_of(WEB_V1[URL_A][1]))} chars, contains draft text)")
print(f"hkB claim: {URL_B}")
print(f"hkC claim: {URL_C}")
print("published 2026-07-01 06:00 UTC (6h after the commit) → passes the freshness gate")

# =============================================================================
hr("PHASE 4 — VALIDATOR ORACLE: two validators grade the SAME claims")
print("V2 fetches a mangled nytimes page (every brief keyword scrubbed out).\n")
WEB_V2 = WEB_V2_TOPICLESS  # oracle grades on the snapshot, so even a keyword-less page agrees

def onchain_of(hotkey):
    return chain.with_block()[hotkey][0]

def oracle_line(label, claim, onchain, brief):
    r = evaluate_article(claim, onchain, registry, brief,
                         fetch_fn=lambda u: fetchmod.fetch(u, epoch=100),
                         search_fn=lambda u: searchmod.in_index(u, epoch=100),
                         serving_hotkey=claim.claimer_hotkey)
    anc = r.evidence.get("snapshot_anchor")
    print(f"  [{label}] {claim.claimer_hotkey}  passed={r.passed}  ${r.usd:>7.2f}  "
          f"reason={r.reason:<8}  level={r.evidence.get('attribution_level')}  "
          f"anchor={anc}  indexed={r.evidence.get('in_index')}")
    return r

claim_A, claim_B, claim_C = serve(store_A)[0], serve(store_B)[0], serve(store_C)[0]
briefs_by_id = {b["id"]: b for b in brief_store.open_briefs()}

for label, web in (("V1", WEB_V1), ("V2", WEB_V2)):
    set_web(web)
    rA = oracle_line(label, claim_A, onchain_of("hkA"), briefs_by_id["b_news"])
    rB = oracle_line(label, claim_B, onchain_of("hkB"), briefs_by_id["b_news"])
    rC = oracle_line(label, claim_C, onchain_of("hkC"), briefs_by_id["b_defi"])
    print()
    # both validators must agree despite V2's mangled page (snapshot anchoring)
    assert rA.passed and rA.usd == 500.0 and rA.evidence["attribution_level"] == 2
    assert rB.passed and rB.usd == 75.0 and rB.evidence["attribution_level"] == 0   # 250 * 0.3
    assert rC.passed and rC.usd == 75.0
print("→ V1 and V2 produced IDENTICAL verdicts. Snapshot anchoring beat the page variance.")
print("→ tier1 text-proof = $500 · tier2 bare = $250×0.3 = $75  (evidence multiplier priced in)")

# =============================================================================
hr("PHASE 5 — VALIDATOR forward(): full pipeline → weights (epoch 1)")
stores = {1: store_A, 2: store_B, 3: store_C}
state_v1 = HeraldState.fresh()

def run_epoch(state, block, web=WEB_V1):
    chain.block = block
    set_web(web)
    v, captured = make_validator(state, stores)
    asyncio.run(fwd.forward(v))
    return captured["w"]

E1 = COMMIT_BLOCK + 2100
w1 = run_epoch(state_v1, E1)
def show(w):
    for uid, name in [(1, "hkA t1 L2"), (2, "hkB t2 L0"), (3, "hkC t2 L0"), (0, "burn")]:
        print(f"   uid {uid} {name:<10} weight = {w.get(uid, 0.0):.4f}")
show(w1)
# installments over 3 epochs: A 500/3=166.7, B 75/3=25, C 75/3=25 ; denom = max(216.7,1000)=1000
assert approx(w1[1], 166.667 / 1000) and approx(w1[2], 25 / 1000) and approx(w1[3], 25 / 1000)
assert approx(w1[0], (1000 - 216.667) / 1000)
print("→ A:B = 500:75 (tier×evidence), client brief b_defi drew from its $300 pool,")
print("  and the unearned remainder ($783/1000) burned to uid 0.")

# =============================================================================
hr("PHASE 6 — MULTI-VALIDATOR: agreement on snapshot-covered fetch variance")
print("V2's live nytimes page dropped the committed DRAFT text but kept the topic.\n")
state_v2 = HeraldState.fresh()
w2 = run_epoch(state_v2, E1, web=WEB_V2_DRAFTLESS)
show(w2)
assert all(approx(w1[u], w2[u]) for u in (0, 1, 2, 3))
print("→ V2 (independent state, draft-less live page) computed the SAME weights as V1. ✓")
print("  Attribution stayed level-2 off the snapshot even though V2's live page lost the draft.")

# ---------------------------------------------------------------------------
hr("PHASE 6b — REGRESSION: liveness-only pay gate agrees on a topic-less live page")
print("V2's live nytimes page now has EVERY brief keyword scrubbed out (topic-less).")
print("The oracle grades A off the snapshot (A wins $500), and the per-epoch installment")
print("gate now checks LIVENESS only — topic + search-index were verified at claim time —")
print("so V2 pays A exactly like V1 instead of forking on its own live fetch.\n")
state_v2b = HeraldState.fresh()
w2b = run_epoch(state_v2b, E1, web=WEB_V2_TOPICLESS)
show(w2b)
assert all(approx(w1[u], w2b[u]) for u in (0, 1, 2, 3))
print("\n→ V2 (topic-less live page) computed the SAME weights as V1 — the pay-gate fork is gone.")
print("  Slashing still uses the live fetch (confirmed 404/410/paid), so security is unchanged.")

# =============================================================================
hr("PHASE 7 — VESTING: installment 2 releases next epoch (article still alive)")
E2 = E1 + VEST_LEN + 1
w1b = run_epoch(state_v1, E2)
show(w1b)
assert approx(w1b[1], 166.667 / 1000) and approx(w1b[2], 25 / 1000)
print("→ same installments release again; vest tracks chain time (3 installments ≈ 3 days).")

# =============================================================================
hr("PHASE 8 — PERSISTENCE: hkB's article dies → clawback + slash")
E3 = E2 + VEST_LEN + 1
DEAD_WEB = {**WEB_V1, URL_B: (404, "")}
w1c = run_epoch(state_v1, E3, web=DEAD_WEB)
show(w1c)
epoch3 = (E3 - 10) // VEST_LEN
slashed_B = state_v1.slash.is_slashed("hkB", epoch3)
statusB = state_v1.vesting.status(article_id(URL_B))
print(f"\n   hkB slashed at epoch {epoch3}: {slashed_B}")
print(f"   hkB vesting status       : {statusB}")
assert w1c[2] == 0.0                      # B earns nothing
assert slashed_B is True                  # and is slashed
assert approx(w1c[1], 166.667 / 1000)     # A keeps vesting (its 3rd/final installment)
assert approx(w1c[3], 25 / 1000)          # C keeps vesting
print("→ hkB's dead article: unvested USD clawed back, hotkey slashed; A & C unaffected.")

hr("RESULT")
print("Operator → Miner → Validator pipeline ran end-to-end on the real code:")
print("  • operator created+funded+SIGNED briefs; a tampered feed was rejected")
print("  • miners committed (real pre_hash) BEFORE publishing, then claimed with snapshots")
print("  • the oracle graded evidence (text-proof ×1.0 vs bare ×0.3) and tiers")
print("  • two validators AGREED across ALL fetch variance (snapshot oracle + liveness-only pay)")
print("  • rewards vested over epochs, the remainder burned, and a dead placement was slashed")
print("\nCross-validator determinism now covers BOTH stages:")
print("  • scoring / attribution — graded on the claim snapshot            (phase 4 + 6)")
print("  • per-epoch payment    — gated on liveness only; topic/index no longer re-forked (phase 6b)")
print("  • slashing             — still on the live fetch (confirmed 404/410/paid); unchanged")
print("\nALL ASSERTIONS PASSED ✓")
