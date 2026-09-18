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
    │ picks a brief and uploads the text before publishing
    │ publishes, then adds the article link
    ▼
Token-gated submissions feed: submission id, brief, link, uploaded text, upload time
    │ read once per daily evaluation epoch
    ▼
Validator oracle verifies each new article on the outlet's own page: registry outlet,
supported fetch, live page, publication inside the brief window and not before the upload,
uploaded text present, not paid content, on topic, search presence
    │
    ▼
30-epoch vesting on the incentive hotkey → liveness checks → clawback on confirmed removal
    │
    ▼
Prepaid client pools + standing briefs → payable USD for the epoch
    │
    ▼
w = min(1, payable USD / USD value of the day's miner emission)
weights {incentive hotkey UID: w, UID 0: 1 − w}; a failed shared step → {UID 0: 1}
    │
    ▼
Signed snapshots → backend confirms the epoch and credits contributor accounts →
contributors claim their share of the incentive hotkey's alpha to a connected wallet
```

## Participants

- **Contributors** are PR firms and PR professionals. They sign in to the Herald website with
  Google, pick a brief, upload the text they will publish before publishing, and add the published
  link. They need no wallet, hotkey or command line to contribute; a wallet connected on the
  website is used only to claim earnings.
- **The Herald backend** (`herald-backend`, a separate service) signs the brief feed, records each
  upload and link against the contributor's account, serves the submissions feed, confirms signed
  epoch snapshots, and credits each account its share of the alpha the incentive hotkey receives.
- **Validators** verify articles, vest their value on the incentive hotkey, and set weights on that
  hotkey and UID 0.
- **The incentive hotkey** is one hotkey registered on the subnet and configured identically on
  every validator as `HERALD_INCENTIVE_HOTKEY`. It is the only UID besides 0 that receives weight.

Registered miner hotkeys receive no weight. The miner neuron (`neurons/miner.py`), `herald/miner/`,
`herald/protocol.py`, `herald/commit.py` and `herald/evidence.py` are deprecated and unused by the
validator.

## Trust boundaries

### Submissions feed

The backend decides which account a submission belongs to; the validator verifies the article.
`herald/validator/news/submissions.py` reads at most the first 10,000 rows and keeps a row only when
its network and netuid are the validator's own, its ids are 1–128 characters of
`[A-Za-z0-9._:-]`, its URL is ASCII `https` of at most 2,048 characters with no query string after
canonicalization, its uploaded text is 300–40,000 characters after trimming, and its upload time is
a positive integer no later than chain time. The uploaded text is used only for verification and is
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
CoinGecko. An input that is missing, non-finite or not positive burns the day.

### Public web

Article fetches permit only HTTP(S), reject private/reserved targets, check every direct redirect,
stream bodies to a configured byte limit, and restrict verification to registry-owned domains.

## Validator pipeline

The epoch orchestration is `herald/validator/news/forward.py`. Scoring runs once per evaluation
epoch (`HERALD_VEST_EPOCH_LEN` blocks, about one day, lagged behind the chain head by
`HERALD_EPOCH_LAG`).

1. Load the Herald ledger and derive the evaluation epoch. If either fails, log the error and change
   nothing.
2. For an epoch that is already scored, keep scores only on UID 0 and the incentive hotkey's current
   UID; any other scores are replaced by all weight on UID 0 (`stale_scores`), which the next
   weight submission sends.
3. Fetch and verify the signed active brief feed. A verified empty feed puts all weight on UID 0
   (`no_briefs`).
4. Resolve the incentive hotkey's UID: the hotkey must be set, registered, different from this
   validator's hotkey, and not at UID 0.
5. Read the scoring block's chain time and price one day of miner emission (`pricing.py`).
6. Load the outlet registry and verify it against the authority anchor.
7. Read the submissions feed, validate its rows, and select new articles with their candidate
   submissions.
8. Verify each selected article's candidates in upload order (`oracle.py`). The first that passes
   starts a vesting entry on the incentive hotkey that records its submission id. An error
   verifying one candidate rejects only that candidate (`verify_error`).
9. Expire entries past their maximum age and entries without a submission id; check the rest for
   liveness and collect released installments.
10. Apply prepaid client pools and sum the payable USD.
11. Build the incentive and burn vector, replace the scores with it, publish result items and a
    signed epoch snapshot, and save the ledger.
12. Submit the latest vector whenever the chain's weight record for this validator's uid is at
    least `HERALD_WEIGHT_RESUBMIT_BLOCKS` blocks old (default 180) and no commit of this hotkey is
    pending reveal, after the base `--neuron.epoch_length` gate. The vector is scored once per epoch
    but submitted on this block cadence, so the chain's copy stays inside the activity cutoff; with
    commit-reveal the record is refreshed about once per tempo.

An error in steps 4–11 burns the epoch (`_burn_epoch`): the ledger returns to its state before the
pass, the scores become all weight on UID 0, the epoch is marked scored so it is not retried, and
nothing is published. Installments not released that day are caught up by the next successful
epoch.

## Selection

`select_new()` groups validated rows by canonical article id and skips articles the vesting ledger
already holds in any status, whatever their rows. An article's rows are its candidates, ordered by
upload time, then submission id; at most `HERALD_MAX_CANDIDATES_PER_ARTICLE` of the earliest uploads
are kept. Articles are ordered by their earliest candidate's upload time, then article id, and at
most `HERALD_MAX_SUBMISSIONS_PER_EPOCH` articles are returned, so a backlog is verified first come,
first served.

The scoring pass walks each article's candidates in order through the whole oracle and credits the
first that passes: the earliest matching draft wins, and an earlier draft that fails does not block
a later one. Each canonical URL is fetched at most once per pass, shared by every candidate and the
liveness check. Each article vests at most once.

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
`SUBMISSION_CREDITED <submission_id> candidate=<i>/<n>`. The uploaded text is never added to the
evidence.

## Vesting and liveness

`VestingLedger` releases an article's value over `HERALD_VEST_EPOCHS`. Each epoch checks liveness
only; topic and search results are not rerun because per-validator page and index variance would
fork installments.

- Alive: release every installment accrued since the last release.
- Hold: the brief is no longer active, or the fetch is inconclusive; release nothing and change
  nothing.
- Dead: a confirmed 404/410, or the page changed to paid content. After
  `HERALD_DEAD_CONFIRM_EPOCHS` consecutive dead epochs the remaining installments are clawed back.

Outlet-specific paid markers are reapplied during liveness checks. There is no slashing. Entries
older than `HERALD_VEST_EPOCHS + HERALD_VEST_GRACE_EPOCHS` epochs expire. Entries without a
submission id, started by earlier releases, are expired and counted in
`LEGACY_VESTING_EXPIRED <n>`.

## Funding and emissions

Client briefs use prepaid USD reward pools recorded in the signed feed. `pool_spent` prevents a
pool from paying more than its funded amount across epochs. Standing briefs contribute their full
current installment value (`apply_reward_pools()` in `emission.py`).

Per-article USD is `HERALD_BASE_PAYOUT_USD × tier multiplier × search factor`, and each article
releases `total_usd / HERALD_VEST_EPOCHS` per evaluation epoch while it remains live. The epoch's
payable USD is the pool-capped sum of released installments.

`daily_miner_usd()` in `pricing.py` values one day of miner emission at the scoring block:

```text
daily_miner_alpha = BLOCKS_PER_DAY (7200) × alpha_out_emission × MINER_EMISSION_SHARE (0.41) × mechanism 0 ratio
daily_usd         = daily_miner_alpha × alpha price in TAO × TAO/USD
```

`incentive_burn_vector()` in `emission.py` returns `([0, uid], [1 − w, w])` with
`w = min(1, payable_usd / daily_usd)` and zero entries dropped, or `([0], [1.0])` when nothing is
payable or there is no usable incentive UID. The validator logs `INCENTIVE_WEIGHT` with `w`, the
payable USD and every pricing input.

At submission, `set_weights()` in `herald/base/validator.py` reads the subnet's MinAllowedWeights
and calls `allowed_emit_vector()`. It normalizes the scores over UID 0 and the incentive hotkey's
current UID (scores anywhere else become all weight on UID 0, logged as `WEIGHT_VECTOR_BURN`),
converts them to u16 without padding or `MaxWeightsLimit` clipping, and raises
`WeightVectorRefused` when the vector is empty, reaches another UID, MinAllowedWeights is unknown,
or the vector is shorter than MinAllowedWeights. A refusal logs `WEIGHT_VECTOR_REFUSED reason=...`
at ERROR and sends no extrinsic. A burn-only vector has one entry, so MinAllowedWeights must be 1.

## State

Validator state has two layers:

- `state.npz`: Bittensor step, scores, hotkeys, and the producing spec version. A version mismatch
  discards scores so a rollout cannot resubmit an older emission model under a new version key.
- `herald_state.json` (schema 2): vesting, pool spending, the last scored epoch, and the last
  successfully submitted weight epoch. The commit index, slash and dispute sections are kept in the
  file format and are no longer updated.

The score checkpoint is restored before initial sync so startup cannot overwrite it with zeroes.
Herald state is atomically replaced after scoring or a burn and again after successful weight
inclusion. The submitted-epoch marker is bookkeeping: when the latest vector is submitted again is
decided from the age of the chain's weight record, not from the marker. Compose persists both files
under the `validator_state` volume.

## Supporting service

`herald/services/app.py` is the legacy JSON brief board, for development and migration only. It
refuses to run with `HERALD_PRODUCTION=true` and requires `HERALD_ENABLE_LEGACY_BRIEF_BOARD=true`.
Production uses `herald-backend`, which serves the signed brief feed, the submissions feed,
snapshot and weight-receipt ingestion, and contributor accounts and earnings.

## Consensus controls

`herald/validator/utils/consensus.py` fingerprints scoring, timing, the emission mode, pricing
constants, submission intake, provider availability, LLM provider/model readiness, fetch limits,
brief-signature policy, and registry trust settings. The fingerprint detects fleet drift; it does
not coordinate deployment. Operators must still roll out changes together.

The following must match across validators:

- epoch, vesting, liveness and payout parameters;
- the emission mode, burn UID, incentive hotkey, price source, miner emission share and blocks per
  day;
- submission intake: the draft-match threshold, publication buffer, maximum article age and
  per-epoch submission cap;
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
