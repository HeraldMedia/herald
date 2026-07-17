# Herald Architecture

Herald is a Bittensor subnet for verified editorial media placement. Its central invariant is that
the same chain state, signed configuration, and public evidence should produce the same reward
weights on every validator.

## System flow

```text
Brief-board operator
    │ creates and funds a signed brief
    ▼
Miner commits (brief, outlet, hotkey, reserved bond=0, evidence hash) on chain
    │ performs the off-chain PR work
    ▼
Miner attaches the published URL and article snapshot
    │ serves ClaimSynapse over its axon
    ▼
Validator oracle verifies commitment, registry, URL, editorial status,
topic, search presence, publication time, and attribution evidence
    │
    ▼
Strongest evidence → earliest commitment → one winner per article and outlet/brief
    │
    ▼
30-epoch vesting → liveness checks → clawback/slash/dispute handling
    │
    ▼
Prepaid client pools + standing placement installments → per-UID USD
    │
    ▼
Normalize rewarded miners to 100% of Bittensor weights
```

## Trust boundaries

### Miner input

The miner controls every `ClaimSynapse` field. `herald/protocol.py` bounds list sizes, string
lengths, integers, evidence, snapshots, and Merkle depth before scoring. The raw transport body is
still read by Bittensor before model validation, so production validators also need a process or
container memory limit.

### Outlet registry

`herald/validator/news/outlets.json` is the assembled 215-outlet edition. Each outlet defines:

- exact domains and optional section paths;
- tier and payout multiplier;
- fetch strategy (`direct`, `proxy[:profile]`, `api:<adapter>`, or fail-closed `disabled`);
- outlet-specific paid-content URL patterns and disclosure markers.

Production editions are signed offline with Ed25519 and bound to an authority hotkey's on-chain
`HRLDREG` commitment. A configured missing or mismatched anchor fails closed.

### Brief feed

The brief board signs the complete validator payload, including funding state, kind, and reward
pool. Validators verify the signature and freshness timestamp. The board signing key is online;
an on-chain brief-edition anchor remains future hardening.

### Public web

Article fetches permit only HTTP(S), reject private/reserved targets, check every direct redirect,
stream bodies to a configured byte limit, and restrict verification to registry-owned domains.
DNS rebinding remains a bounded residual risk.

## Validator pipeline

The epoch orchestration is `herald/validator/news/forward.py`.

1. Load persistent commit, vesting, slash, dispute, pool, and last-scored state.
2. Derive the evaluation epoch from lagged chain height.
3. Fetch and verify the signed active brief feed.
4. Read on-chain commitments and the registry authority anchor.
5. Load and verify the outlet registry.
6. Pull claims concurrently from miner axons.
7. Merge public-board reconciliation hints and validate them as bounded claims.
8. Run winner selection and reject articles not provably published after the commitment.
9. Start new vesting entries and process on-chain disputes.
10. Recheck active placements for liveness and paid-content swaps.
11. Apply prepaid client pools and aggregate placement/dispute value by miner.
12. Normalize rewarded miners to the full weight vector, publish results, and persist state.
13. Submit that vector once for the scored Herald epoch when Bittensor's chain gate permits it.

An explicitly valid empty brief feed clears local scores. The weight writer skips an empty vector
instead of letting the SDK convert it into uniform rewards.

## Verification oracle

`evaluate_article()` in `herald/validator/news/oracle.py` is ordered cheapest-first:

1. Serving-hotkey and attribution-evidence integrity
2. Commitment hash
3. Registry version and outlet lookup
4. Strategy-aware page fetch
5. Miner snapshot anchoring
6. Generic and outlet-specific paid-content detection
7. Rules-first topic matching
8. Search-index check
9. Attribution-evidence grading and USD calculation

Direct/proxy fetches anchor the miner snapshot inside the validator's full page. Publisher API
adapters reverse the direction: the authoritative excerpt must appear inside the miner's snapshot.
Topic metadata for API adapters remains publisher-controlled.

## Attribution and winner selection

Commitments can bind three evidence levels:

- Level 2: precommitted draft or quote appears in the article.
- Level 1: precommitted byline and bounded publication window match.
- Level 0: bare prediction.

Selection is strongest evidence, then earliest observed commitment, then lowest UID. If two
different hotkeys commit substantially overlapping level-2 text for the same article, both are
demoted because shared copy proves campaign involvement but not individual causation.

Only one article wins each `(outlet, brief)` placement slot.

## Vesting, slashing, and disputes

`VestingLedger` releases a placement over `HERALD_VEST_EPOCHS`. Each epoch checks liveness only;
topic and search decisions are not rerun because per-validator page/index variance would fork
installments.

- Alive: release accrued installments.
- Hold: withhold while evidence is inconclusive or the brief is unavailable.
- Confirmed dead or changed to paid content: claw back remaining installments and slash.

Outlet-specific paid markers are reapplied during persistence, preventing a page from being changed
to an outlet's branded-content format after initial acceptance.

Disputes use an on-chain `HRLDDIS` commitment. They are enabled only with a fleet-wide pinned model.
An upheld dispute rewards the eligible filer from forfeited vesting; a rejected
griefing dispute slashes the filer.

## Funding and emissions

Client briefs use prepaid USD reward pools recorded in the signed feed. `pool_spent` prevents a
pool from paying more than its funded amount across epochs. Standing placements contribute their
full current installment value.

There is no funding boost or `HRLDFUND` multiplier. Per-article USD is based on tier, search status,
and attribution evidence. Each placement releases `total_usd / HERALD_VEST_EPOCHS` per evaluation
epoch while it remains live. The validator sums all current installments by miner, including
overlapping placements, and `compute_weights()` normalizes those positive totals to 100%.

## State

Validator state has two layers:

- `state.npz`: Bittensor step, scores, hotkeys, and the producing spec version. A version mismatch
  discards scores so a rollout cannot resubmit an older emission model under a new version key.
- `herald_state.json`: commit index, vesting, slashing, disputes, pool spending, the last scored
  epoch, and the last successfully submitted weight epoch.

The score checkpoint is restored before initial sync so startup cannot overwrite it with zeroes.
Herald state is atomically replaced after successful scoring and again after successful weight
inclusion. The separate submission marker prevents Bittensor's shorter weight-update interval from
resubmitting one unchanged daily allocation. Compose persists both files under the
`validator_state` volume.

Miner `claims.json` contains commitment nonces and is written atomically with mode `0600`.

## Supporting service

`herald/services/app.py` exposes:

- signed validator briefs and public open briefs;
- token-gated brief administration and funding confirmation;
- token-gated result and reveal ingestion;
- public verified placements, leaderboard, statistics, registry, and reporting export;
- registry draft staging for later offline signing;
- an informational dispute mirror.

Writes fail closed when their token is unset. Reveals are protected on both read and write. The
service should run behind TLS, a request-body limit, and persistent storage.

## Consensus controls

`herald/validator/utils/consensus.py` fingerprints scoring, timing, provider availability, LLM
provider/model readiness, fetch limits, brief-signature policy, and registry trust settings. The
fingerprint detects fleet drift; it does not coordinate deployment. Operators must still roll out
changes together.

The following must match across validators:

- epoch, vesting, weight-slashing, dispute-eligibility, and payout parameters;
- fetch/search provider availability, quorum, and limits;
- outlet registry edition, signing key, and authority anchor;
- brief signing key and freshness policy;
- optional LLM provider, credentials availability, and pinned model.

## Verification

```bash
source .venv/bin/activate
python -m pytest -q
python scripts/e2e_simulation.py
docker compose config -q
bash -n entrypoint.sh scripts/*.sh
```

The localhost `herald-sim` sibling repository exercises direct, proxy, and publisher-API behavior
without weakening production SSRF guards.
