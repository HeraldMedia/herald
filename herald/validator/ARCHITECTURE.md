# Herald Architecture

Herald is a Bittensor subnet for verified editorial media placement. Its central invariant is that
the same chain state, signed configuration, backend submissions and public evidence should produce
the same weights on every validator.

## System flow

```text
Brief operator (herald-backend)
    │ creates, funds and signs briefs
    ▼
Contributor on the Herald website (Google sign-in)
    │ registers their own hotkey on the subnet with a wallet extension
    │ picks a brief and uploads the text before publishing; the coldkey that owns the
    │ hotkey signs the submission
    │ publishes, then adds the article link
    ▼
Token-gated submissions feed: submission id, brief, link, uploaded text, upload time,
hotkey, coldkey, signature
    │ read once per daily evaluation epoch
    ▼
Validator checks the signature, and on chain that the coldkey owns the hotkey
    │
    ▼
Validator oracle verifies each new article on the outlet's own page: registry outlet,
supported fetch, live page, publication inside the brief window and not before the upload,
uploaded text present, not paid content, on topic, search presence
    │
    ▼
30-epoch vesting on the contributor's hotkey → liveness checks → clawback on confirmed
removal; releases hold while the hotkey is not registered
    │
    ▼
Prepaid client pools + standing briefs → payable USD per miner UID for the epoch
    │
    ▼
U = total payable USD, d = USD value of the day's miner emission
weights: each miner UID usd / max(U, d); UID 0 receives 1 − U / d when U < d (burned)
a day that is not scored → {UID 0: 1}
    │
    ▼
Signed snapshots → backend confirms the epoch;
the chain emits each miner UID's share to the contributor's hotkey
```

## Participants

- **Contributors** are PR firms and PR professionals. They sign in to the Herald website with
  Google and register their own hotkey on netuid 69 from the website with a wallet extension
  (Talisman, SubWallet or Polkadot.js). For each submission they pick a brief, upload the text they
  will publish before publishing, sign the submission with the coldkey that owns their hotkey, and
  add the published link. They run no miner server, axon or command line.
- **The Herald backend** (`herald-backend`, a separate service) signs the brief feed, records each
  upload, signature and link, serves the submissions feed, and confirms signed epoch snapshots. It
  cannot move a submission to another hotkey: the contributor's signature covers the hotkey.
- **Validators** check each submission's signature and hotkey owner, verify articles, vest their
  value on the contributors' hotkeys, and set weights on those hotkeys' UIDs and UID 0.

A miner hotkey earns only through signed submissions credited to it; the validator sends no queries
to miners. The miner neuron (`neurons/miner.py`), `herald/miner/`, `herald/protocol.py`,
`herald/commit.py` and `herald/evidence.py` are deprecated and unused by the validator.

## Trust boundaries

### Submissions feed

Each row names the contributor's hotkey and carries a signature, by the coldkey that owns it, over
the network, netuid, brief, SHA-256 of the uploaded text and hotkey
(`herald/validator/news/signatures.py`). The validator rebuilds that message from the row, hashing
the text itself, so the backend can neither move a submission to another hotkey nor change the text
it vouches for. `herald/validator/news/submissions.py` reads at most the first 10,000 rows and keeps
a row only when its network and netuid are the validator's own, its ids are 1–128 characters of
`[A-Za-z0-9._:-]`, its URL is ASCII `https` of at most 2,048 characters with no query string after
canonicalization, its uploaded text is 300–40,000 characters after trimming, its upload time is a
positive integer no later than chain time, and its signature verifies. Whether the coldkey owns the
hotkey is not taken from the row: it is read from the chain at the scoring block
(`get_hotkey_owners()` in `chain.py`, `SubtensorModule.Owner`), and a candidate whose coldkey is not
the owner is rejected (`hotkey_not_owned`). The uploaded text is used only for verification and is
never published, stored or logged.

### Outlet registry

`herald/validator/news/outlets.json` is the assembled 215-outlet edition. Each outlet defines:

- exact domains and optional section paths;
- tier and payout multiplier;
- fetch strategy (`direct`, `proxy[:profile]`, `api:<adapter>`, or fail-closed `disabled`);
- outlet-specific paid-content URL patterns and disclosure markers.

New submissions are accepted only from `direct` and `proxy` outlets, because every content check
runs on the page the validator fetches itself. Production editions are signed offline with Ed25519
and bound to an authority hotkey's on-chain `HRLDREG` commitment, the only commitment a validator
reads. A configured missing or mismatched anchor fails closed.

### Brief feed

The brief board signs the complete validator payload, including funding state, kind, and reward
pool. Validators verify the signature and freshness timestamp.

### Chain and price inputs

The scoring block's timestamp, the subnet's alpha price, its per-block alpha emission and its
mechanism emission split are read from the chain at the scoring block; TAO/USD comes from
CoinGecko. An input that is missing, non-finite or not positive fails the day. The owners of the
candidates' hotkeys are read at the scoring block too, and a failed read fails the day.

### Public web

Article fetches permit only HTTP(S), reject private/reserved targets, check every direct redirect,
stream bodies to a configured byte limit, and restrict verification to registry-owned domains.

## Validator pipeline

The epoch orchestration is `herald/validator/news/forward.py`. Scoring runs once per evaluation
epoch (`HERALD_VEST_EPOCH_LEN` blocks, about one day, lagged behind the chain head by
`HERALD_EPOCH_LAG`).

1. Load the Herald ledger and derive the evaluation epoch. If either fails, log the error and change
   nothing.
2. For an epoch that is already scored, keep its scores on UID 0 and the miner UIDs it was scored
   for (`weight_hotkeys`). Empty scores, or scores on any other UID, are replaced by all weight on
   UID 0 (`stale_scores`), and the next weight submission sends the replacement.
3. Fetch and verify the signed active brief feed. A verified empty feed (`no_briefs`) puts all
   weight on UID 0.
4. Read the scoring block's chain time and price one day of miner emission (`pricing.py`).
5. Load the outlet registry and verify it against the authority anchor.
6. Read the submissions feed, validate its rows (signatures included), and select new articles
   with their candidate submissions.
7. Read the owner of each candidate's hotkey from the chain at the scoring block.
8. Verify each selected article's candidates in upload order. A candidate whose coldkey does not
   own its hotkey is skipped (`hotkey_not_owned`); the others go through the oracle (`oracle.py`).
   The first that passes starts a vesting entry on its hotkey that records its submission id and
   signing coldkey. An error verifying one candidate rejects only that candidate (`verify_error`).
9. Expire entries past their maximum age and entries not started from a signed submission; map
   each remaining entry's hotkey to its current UID, check liveness and collect released
   installments. A live article whose hotkey is not registered holds.
10. Apply prepaid client pools and sum the payable USD per miner UID.
11. Build the weight vector (`miner_weight_vector()`), replace the scores with it, record its miner
    UIDs with their hotkeys (`weight_hotkeys`), publish result items and a signed epoch snapshot
    (schema 2), and save the ledger.
12. Submit the latest vector whenever the chain's weight record for this validator's uid is at
    least `HERALD_WEIGHT_RESUBMIT_BLOCKS` blocks old (default 180) and no commit of this hotkey is
    pending reveal, after the base `--neuron.epoch_length` gate. The vector is scored once per epoch
    but submitted on this block cadence, so the chain's copy stays inside the activity cutoff; with
    commit-reveal the record is refreshed about once per tempo.

An error in steps 4–11 fails the epoch (`_fail_epoch`): the ledger returns to its state before the
pass, the epoch is marked scored so it is not retried, and nothing is published. The scores become
all weight on UID 0 (`EPOCH_BURN epoch=<e> reason=<reason>`). Installments not released that day are
caught up by the next successful epoch.

## Selection

`select_new()` groups validated rows by canonical article id and skips articles the vesting ledger
already holds in any status, whatever their rows. An article's rows are its candidates, ordered by
upload time, then submission id; at most `HERALD_MAX_CANDIDATES_PER_ARTICLE` of the earliest uploads
are kept. Articles are ordered by their earliest candidate's upload time, then article id, and at
most `HERALD_MAX_SUBMISSIONS_PER_EPOCH` articles are returned, so a backlog is verified first come,
first served.

The scoring pass walks each article's candidates in order through the ownership check and the whole
oracle and credits the first that passes: the earliest matching draft wins, and an earlier draft
that fails does not block a later one. Each canonical URL is fetched at most once per pass, shared
by every candidate and the liveness check. Each article vests at most once.

## Verification oracle

`verify_article()` in `herald/validator/news/oracle.py` runs exact checks in order and stops at the
first failure. The brief must be active before it is called (`brief_not_active`).

1. Registry outlet lookup (`outlet_not_listed`)
2. Supported fetch strategy, `direct` or `proxy` (`outlet_not_supported`)
3. Strategy-aware page fetch (`url_not_live`)
4. Publication time present (`publication_date_unverifiable`)
5. Publication window (`published_outside_window`): no later than chain time and at most
   `HERALD_MAX_ARTICLE_AGE_DAYS` before it; for a brief with an end date that is not a standing
   brief, from start date minus `HERALD_PUBLISH_BUFFER_DAYS` at 00:00 UTC through end date
   23:59:59 UTC; a publication time that is not exact counts on the date the page states
6. Published no earlier than the upload (`published_before_upload`): at or after the upload time
   when the page states an exact publication time (a time of day with `Z` or an explicit UTC
   offset), otherwise on or after the upload's UTC day. A date alone, a time with no offset, or
   exactly 00:00:00 in the stated offset (how many sites render a date alone) is not exact, and is
   judged by the date the page states, in its own offset (`published_date`): for example
   `2026-09-08T00:00:00+02:00` is 8 September. The evidence records `published_exact` and
   `published_date`.
7. Draft match (`draft_mismatch`): the share of the uploaded text's normalized five-word shingles
   found in the fetched article body must be at least `HERALD_DRAFT_MATCH_THRESHOLD`
8. Generic and outlet-specific paid-content detection (`paid_not_real_news`)
9. Rules-first topic matching (`topic_mismatch`)
10. Search-index check and USD value

Each candidate tried logs `SUBMISSION_RESULT <submission_id> <reason>`, where the reason is `ok` or
the first failed check, and the credited candidate logs
`SUBMISSION_CREDITED <submission_id> candidate=<i>/<n> hotkey=<ss58>`. The uploaded text is never
added to the evidence.

## Vesting and liveness

`VestingLedger` releases an article's value over `HERALD_VEST_EPOCHS`. Each epoch checks liveness
only; topic and search results are not rerun because per-validator page and index variance would
fork installments.

- Alive: release every installment accrued since the last release.
- Hold: the brief is no longer active, or the fetch is inconclusive; release nothing and change
  nothing.
- Unregistered: the entry's hotkey is not in the metagraph. A live article holds and is counted in
  `VESTING_HELD_UNREGISTERED <n>`; once the hotkey registers again, its missed installments are
  released on whichever UID it then holds.
- Dead: a confirmed 404/410, or the page changed to paid content. After
  `HERALD_DEAD_CONFIRM_EPOCHS` consecutive dead epochs the remaining installments are clawed back.

Outlet-specific paid markers are reapplied during liveness checks. There is no slashing. Entries
older than `HERALD_VEST_EPOCHS + HERALD_VEST_GRACE_EPOCHS` epochs expire, which bounds how long a
held article can catch up. Entries not started from a signed submission (no submission id or no
signing coldkey), started by earlier releases, are expired and counted in
`LEGACY_VESTING_EXPIRED <n>`.

## Funding and emissions

Client briefs use prepaid USD reward pools recorded in the signed feed. `pool_spent` prevents a
pool from paying more than its funded amount across epochs. Standing briefs contribute their full
current installment value (`apply_reward_pools()` in `emission.py`).

Per-article USD is `HERALD_BASE_PAYOUT_USD × tier multiplier × search factor`, and each article
releases `total_usd / HERALD_VEST_EPOCHS` per evaluation epoch while it remains live. Each miner
UID's payable USD is the pool-capped sum of its articles' released installments.

`daily_miner_usd()` in `pricing.py` values one day of miner emission at the scoring block:

```text
daily_miner_alpha = BLOCKS_PER_DAY (7200) × alpha_out_emission × MINER_EMISSION_SHARE (0.41) × mechanism 0 ratio
daily_usd         = daily_miner_alpha × alpha price in TAO × TAO/USD
```

`miner_weight_vector()` in `emission.py` builds the epoch's vector from each miner UID's payable USD
`usd`, their total `U` and `d = daily_usd`:

- `U <= d`: each miner UID receives `usd / d` and UID 0 receives `1 − U / d`, which is burned;
- `U > d`: each miner UID receives `usd / U` and UID 0 nothing;
- `([0], [1.0])` when nothing is payable or `d` is not a finite positive number.

Zero entries are dropped and UIDs are ascending. The validator logs `EPOCH_WEIGHTS` with the number
of paid miner UIDs, the payable USD, UID 0's weight (`burn`) and every pricing input, then one
`MINER_WEIGHT` line per paid miner UID with its hotkey, USD and weight.

At submission, `set_weights()` in `herald/base/validator.py` reads the subnet's MinAllowedWeights
and calls `allowed_emit_vector()`. Only UID 0 and the miner UIDs the latest epoch was scored for,
each while it still holds the hotkey recorded in `weight_hotkeys`, may receive weight: a score on
any other UID, or on a UID another hotkey has since taken, moves to UID 0
(`WEIGHT_VECTOR_BURN reason=hotkey_changed`), so a miner's pay never reaches another account. The
shares are converted to u16 without padding or `MaxWeightsLimit` clipping, and
`WeightVectorRefused` is raised when the vector is empty, reaches a UID that is not allowed,
MinAllowedWeights is unknown, or the vector is shorter than MinAllowedWeights. A refusal logs
`WEIGHT_VECTOR_REFUSED reason=...` at ERROR and sends no extrinsic. The burn-only vector has one
entry, so MinAllowedWeights must be 1.

## State

Validator state has two layers:

- `state.npz`: Bittensor step, scores, hotkeys, and the producing spec version. A version mismatch
  discards scores so a rollout cannot resubmit an older emission model under a new version key.
- `herald_state.json` (schema 2): vesting, pool spending, the last scored epoch, the last
  successfully submitted weight epoch and, while a miner UID holds weight, the latest scored
  epoch's miner UIDs with their hotkeys (`weight_hotkeys`). The commit index, slash and dispute
  sections are kept in the file format and are no longer updated.

The score checkpoint is restored before initial sync so startup cannot overwrite it with zeroes.
Herald state is atomically replaced after scoring or a failed pass and again after successful
weight inclusion. The submitted-epoch marker is bookkeeping: when the latest vector is submitted again is
decided from the age of the chain's weight record, not from the marker. Compose persists both files
under the `validator_state` volume.

## Supporting service

`herald/services/app.py` is the legacy JSON brief board, for development and migration only. It
refuses to run with `HERALD_PRODUCTION=true` and requires `HERALD_ENABLE_LEGACY_BRIEF_BOARD=true`.
Production uses `herald-backend`, which serves the signed brief feed, the submissions feed,
snapshot and weight-receipt ingestion, and contributor accounts.

## Consensus controls

`herald/validator/utils/consensus.py` fingerprints scoring, timing, the emission mode
(`miner_hotkeys_v1`), pricing constants, submission intake (`backend_submissions_signed_v3`),
provider availability, LLM provider/model readiness, fetch limits, brief-signature policy, and
registry trust settings. The fingerprint detects fleet drift; it does
not coordinate deployment. Operators must still roll out changes together.

The following must match across validators:

- epoch, vesting, liveness and payout parameters;
- the emission mode, burn UID, price source, miner emission share and blocks per day;
- submission intake: the intake version, the draft-match threshold, publication buffer, maximum
  article age, and per-epoch submission and per-article candidate caps;
- fetch/search provider availability, quorum, and limits;
- outlet registry edition, signing key, and authority anchor;
- brief signing key and freshness policy;
- optional LLM provider, credentials availability, and pinned model.

## Verification

```bash
source .venv/bin/activate
python -m pytest -q
docker compose config -q
bash -n entrypoint.sh scripts/*.sh
```

The localhost `herald-sim` sibling repository exercises direct, proxy, and publisher-API behavior
without weakening production SSRF guards.
