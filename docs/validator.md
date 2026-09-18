# Herald Validator Guide (Bittensor netuid 69 · finney)

A Herald **validator** runs the code-only **verification oracle**: it pulls open briefs from the
subnet backend, reads the articles contributors submitted through the Herald website, verifies each
one against the outlet's own page, vests its value on one **incentive hotkey**, sets weights on that
hotkey and UID 0 (the rest of the day's miner emission is burned), and publishes signed epoch
snapshots. Registered miner hotkeys receive no weight. **No GPU / no ML** — it is network-I/O bound
(web fetches, search-API calls, chain RPC, one price API). The optional LLM judge is a *remote* API.

> Deploying the whole stack? You also run the backend (`herald-backend/`). Keep your
> deployment-specific values (endpoints, tokens, fingerprint, on-chain anchor) in a private
> runbook **outside this repo**, not committed here.

---

## 1. Hardware

| Resource | Minimum (`min_compute.yml`) | Recommended |
|---|---|---|
| CPU | 2 vCPU | **4 vCPU** |
| RAM | 8 GB | **8–16 GB** |
| Disk | — | **50 GB SSD** |
| GPU | none | **none** |
| Network | — | static public IP, inbound axon **8092**, ≥100 Mbps, generous egress |
| OS | — | Ubuntu 22.04 / 24.04 |
| TAO | — | registration burn **+ stake for a validator permit** (or weights don't count) |

---

## 2. Prerequisites

- A hotkey **registered on netuid 69** with enough **stake** to hold a validator permit.
- **API keys** (the oracle's outside-data providers):
  - **`SCRAPINGBEE_API_KEY` — required** whenever the signed registry contains `proxy:` outlets
    (it currently does — ~49 of 215: Bloomberg, NYT, FT, Economist, …). It's the *fetch* provider
    for anti-bot / paywalled / JS-rendered sites. Without it you can't verify those outlets and you
    fork from the fleet.
  - **One search provider — `SERPAPI_API_KEY` *or* `BRAVE_API_KEY`** (the *search-index* check).
    Brave is markedly cheaper. Pick **one** and standardize it fleet-wide.
- **Trust anchors** from the subnet operator:
  - Backend endpoint (canonical): `https://api.heraldmedia.ai`
  - Brief-feed pubkey: `a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a`
  - Registry pubkey: `9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6`
  - Registry **authority hotkey** (SS58, public / fleet-wide constant): `5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5`
  - **Incentive hotkey** (SS58, public / fleet-wide constant): the value of
    `HERALD_INCENTIVE_HOTKEY`, provided by the subnet operator.
  - **Results credentials** — the shared results token, or a per-validator write and read token
    pair (secret, operator-provided). The read credential is what reads the submissions feed.
  - The **signed registry file** (byte-identical to what the backend serves).
- **Outbound HTTPS to CoinGecko** for the TAO/USD price (public API, no key).
- The subnet's **MinAllowedWeights must be 1** (§8.6).

Rough cost: server ~$20–40/mo + API keys (ScrapingBee is the main line item), scaling with subnet
submission volume. Stake is locked capital, not spend, and the validator earns emissions.

---

## 3. Consensus fingerprint (read this first)

Weights only agree if every validator uses an **identical** scoring configuration. That config is
hashed into a short **consensus fingerprint**, logged at startup and attached to every published
result. The set that must match fleet-wide includes: the **search provider(s)**, ScrapingBee on/off,
any `api:*` adapters, the LLM-judge setting + pinned model, all scoring tunables (epoch lengths,
payout, floors, the publication window, the draft-match threshold, the per-epoch article cap and
the per-article candidate cap), the incentive hotkey and pricing constants (§8), and the trust
anchors (pubkeys, authority hotkey). Release `0.2.0` changes the fingerprint (§8.7).

Derive it and pin it on the validator **and** give it to the backend operator
(`HERALD_EXPECTED_CONSENSUS_FP` on both):
```bash
# Docker (loads .env, no wallet needed):
docker compose --profile validator run --rm --no-deps --entrypoint python \
  validator -m herald.production fingerprint
# or bare-metal:  set -a; source .env; set +a; python -m herald.production fingerprint
```
A mismatch = weight divergence, and the backend `/ready` won't confirm. **Standardize the provider
set before you launch.**

---

## 4. Setup

### 4.1 Install + wallet + register + stake
```bash
git clone <this repo> herald && cd herald
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install --no-build-isolation -e .

btcli wallet new_coldkey --wallet.name herald_vali
btcli wallet new_hotkey  --wallet.name herald_vali --hotkey v1
btcli subnet register --netuid 69 --wallet.name herald_vali --wallet.hotkey v1 --network finney
btcli stake add       --netuid 69 --wallet.name herald_vali --wallet.hotkey v1 --amount <TAO>
```

### 4.2 Place the signed registry + write `.env`
```bash
mkdir -p /secure/herald
curl -s https://api.heraldmedia.ai/registry/outlets.json -o /secure/herald/outlets.signed.json
```
Copy `deploy/validator.env.production.example` to `.env` and fill the blanks. Its settings are
shown below — the public trust anchors (endpoint + pubkeys) are baked in; you supply your
wallet/IP, the operator-provided incentive hotkey and the operator-provided secrets (results token,
API keys):
```ini
# Herald validator — production .env (Bittensor netuid 69, finney).
# Copy to .env, fill the blanks (your wallet/IP + operator-provided secrets), then: chmod 600 .env
# For every available setting (incl. the consensus-critical scoring tunables), see root .env.example.
HERALD_PRODUCTION=true
HERALD_PRODUCTION_NETUID=69
NETUID=69
SUBTENSOR_NETWORK=finney
# The provided compose connects with --subtensor.network (finney, as production preflight requires)
# and passes no chain endpoint. HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT adds a second finney node that
# is used only for the pending weight-commit check (see Troubleshooting).

# ── Wallet: registered on netuid 69 with stake for a validator permit ──
WALLET_NAME=
HOTKEY_NAME=
# This host's public IP (announced to the chain):
AXON_EXTERNAL_IP=
VALIDATOR_AXON_PORT=8092

# ── Canonical subnet backend + trust anchors (the pubkeys are public) ──
HERALD_BRIEFS_ENDPOINT=https://api.heraldmedia.ai/api/v2/validator/briefs
HERALD_BRIEFS_PUBKEY=a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a
HERALD_REQUIRE_SIGNED_BRIEFS=true
HERALD_RESULTS_ENDPOINT=https://api.heraldmedia.ai
# Operator-provided (secret) — the shared token validators present to the backend's results routes:
HERALD_RESULTS_TOKEN=
# Or scoped credentials, once the operator issues them: the write token for reports and the read
# token for the submissions feed. Each one set replaces the shared token for its routes.
# HERALD_RESULTS_WRITE_TOKEN=
# HERALD_RESULTS_READ_TOKEN=

# ── Incentive hotkey: the only UID besides 0 that receives weight (public, fleet-wide) ──
HERALD_INCENTIVE_HOTKEY=

# ── Signed outlet registry ──
# Docker:      HERALD_REGISTRY_HOST_FILE is the host file; compose bind-mounts it read-only at
#              HERALD_REGISTRY_PATH inside the container.
# Bare-metal:  set HERALD_REGISTRY_PATH to the host file directly and leave HOST_FILE unset.
HERALD_REGISTRY_HOST_FILE=/secure/herald/outlets.signed.json
HERALD_REGISTRY_PATH=/run/registry/outlets.signed.json
HERALD_REGISTRY_PUBKEY=9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6
# The subnet's registry authority hotkey — its on-chain HRLDREG commitment activates registry
# editions. A fleet-wide CONSENSUS constant (part of the fingerprint): identical on every validator,
# and public. Currently the owner hotkey (uid 0); a dedicated hotkey is cleaner, but reusing the
# owner key is fine for bootstrap (its metadata commitment slot doesn't collide with weight-setting).
HERALD_REGISTRY_AUTHORITY_HOTKEY=5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5
HERALD_REQUIRE_SIGNED_REGISTRY=true

# ── Outside-data providers — CONSENSUS-CRITICAL: enable the identical set on every validator ──
# Required — the signed registry contains proxy: outlets (fetch provider):
SCRAPINGBEE_API_KEY=
# One search provider is required; pick BRAVE or SERPAPI and use it fleet-wide:
BRAVE_API_KEY=
# SERPAPI_API_KEY=
# HERALD_NYT_API_KEY=          # only if the registry contains api:nyt outlets
HERALD_QUORUM_THRESHOLD=1
HERALD_ALLOW_LOCAL_FETCH=false
HERALD_USE_LLM_JUDGE=false
DISABLE_AUTO_UPDATE=true

# ── Scoring cadence + consensus fingerprint ──
# Poll for scoring every N steps (~N*60s). Scoring itself stays once per epoch and is NOT in the
# fingerprint; the default (240) can delay the first snapshot by hours, so 10 is a sensible start.
HERALD_VALIDATOR_STEPS_INTERVAL=10
# Compute with `python -m herald.production fingerprint`; set the SAME value here AND on the backend:
HERALD_EXPECTED_CONSENSUS_FP=
```
`chmod 600 .env`.

### 4.3 Run
```bash
docker compose --profile validator up -d --build validator
docker compose --profile validator logs -f validator
# bare-metal alternative: ./scripts/setup_env.sh && ./scripts/run_validator.sh   (venv + pm2)
```

The subnet operator must also **enroll your hotkey as a reporter** on the backend
(`POST /admin/reporters`) for your signed snapshots to count toward epoch confirmation.

---

## 5. Production preflight — what fails closed

With `HERALD_PRODUCTION=true`, the neuron refuses to start unless (see `herald/production.py`):
finney + netuid 69; **signed registry required**, its pubkey + authority hotkey set, and the file's
signature verifies; **signed briefs required** + briefs pubkey set; briefs/results endpoints set and
non-localhost; a results write and read credential (scoped, or the shared token);
**`HERALD_INCENTIVE_HOTKEY` set** to a valid SS58 address that differs from
`HERALD_REGISTRY_AUTHORITY_HOTKEY`; **ScrapingBee key present** if the registry has `proxy:` outlets
(and an NYT key if it has `api:nyt`); **at least one search provider**;
`HERALD_EXPECTED_CONSENSUS_FP` set and matching the computed fingerprint; and a **live on-chain
`HRLDREG` registry anchor** exists. Any failure prints `production preflight failed: <exact reasons>`.

Preflight sees only the environment. Whether the incentive hotkey is registered, differs from this
validator's own hotkey and is not at UID 0 is checked at every scoring pass instead, and a failure
there burns the day (§8.5).

---

## 6. Topic gating and the LLM judge

An article only pays if it is **on the brief's topic**. That check is rules-first:

```python
keywords = brief.get("keywords") or []
if keywords and any(k in text): return True     # rule hit
if judge_fn: ...                                 # optional LLM fallback
return not keywords                              # NO keywords -> everything passes
```

So a brief with **no keywords** and **no judge** accepts any article as on-topic. Close it one of
two ways:

**a) Keywords on the brief (no extra config).** The operator sets them per brief; validators need
nothing. Cheapest and fully deterministic.

**b) The LLM judge.** Handles briefs whose topic is hard to express as substrings.
**Consensus-critical — every validator must match exactly, or weights diverge:**

```ini
HERALD_USE_LLM_JUDGE=true
HERALD_REF_MODEL_ID=<pinned model id>   # required; without it the tier stays OFF
LLM_PROVIDER=chutes                     # or openrouter
CHUTES_API_KEY=<key>                    # or OPENROUTER_API_KEY
```

Rolling it out:
1. Agree the provider **and** the pinned model across the fleet — `use_llm_judge`, `ref_model_id`,
   `llm_provider` and `llm_provider_ready` are all in the consensus fingerprint.
2. Recompute the fingerprint (`python -m herald.production fingerprint`) and set the new value on
   **every validator and the backend** (`HERALD_EXPECTED_CONSENSUS_FP`).
3. Recreate every validator together (`docker compose --profile validator up -d validator`; a
   plain `docker restart` keeps the old environment), and recreate the backend so it checks the new
   value (`docker compose up -d api indexer` in herald-backend). A validator left on the old setting
   will fork its weights.

Note the judge is a *fallback*, not a veto: if it errors or is unsure it returns `None`, and an
un-keyworded brief then falls through to accepting the article. Keywords remain the safety net.

## 7. Epoch confirmation & quorum

Validators publish **hotkey-signed epoch snapshots** to `POST /api/v3/validator/snapshots`. The
backend confirms an epoch when **`HERALD_QUORUM_REQUIRED`** matching enrolled reporters agree, and
confirmed epochs are what the backend settles contributor submissions from.

- **One confirming operator:** set `HERALD_QUORUM_REQUIRED=1` on the backend when a single
  operator's validator is the only enrolled reporter; a higher value cannot be met by one reporter.
- **Independent validators:** raise it to 2 or more once independent validators are enrolled, so
  two validators corroborate each epoch before its results are canonical.

An epoch burned by a failed step publishes no results or snapshot (§8.5).

---

## 8. Contributor submissions, the incentive hotkey and weights

Contributors (PR firms and PR professionals) submit through the Herald website with Google sign-in:
they pick a brief, upload the text they will publish before publishing, then add the published link.
The backend decides which account a submission belongs to. The validator verifies the article
itself, vests its value on one incentive hotkey, and sets weight on that hotkey and UID 0 only.
Registered miner hotkeys receive no weight, and the validator sends no queries to miners.

### 8.1 Submissions feed and read credential

- Each scoring pass reads
  `GET {HERALD_RESULTS_ENDPOINT}/api/v4/validator/submissions?network=<network>&netuid=<netuid>`
  with the read credential, `HERALD_RESULTS_READ_TOKEN` or the shared `HERALD_RESULTS_TOKEN`
  (10 s timeout). It is read after the brief feed, the incentive hotkey check and pricing.
  Production preflight requires `HERALD_RESULTS_ENDPOINT` and a read credential.
- Each row carries `submission_id`, `network`, `netuid`, `brief_id`, `url`, `draft_text` (the
  uploaded text) and `uploaded_ts` (unix seconds, UTC). Of the first 10,000 rows, a row is used only
  when:
  - `network` and `netuid` are this validator's;
  - `submission_id` and `brief_id` are 1–128 characters of `A–Z a–z 0–9 . _ : -`;
  - `url` is an ASCII `https` URL of at most 2,048 characters with no query string once tracking
    parameters are removed;
  - `draft_text` is 300–40,000 characters after trimming surrounding whitespace;
  - `uploaded_ts` is a positive integer no later than the scoring block's chain time.
- Valid rows are grouped by canonical article. Several contributors may submit the same article:
  its rows are its candidates, ordered by upload time (`uploaded_ts`), then submission id, and at
  most `HERALD_MAX_CANDIDATES_PER_ARTICLE` of the earliest uploads are kept. An article already in
  the vesting ledger (in any status) is skipped whatever its candidates: credit, once decided, is
  final.
- Articles are verified first come, first served: in order of their earliest candidate's upload
  time, then canonical article id, at most `HERALD_MAX_SUBMISSIONS_PER_EPOCH` articles per epoch.
  The rest wait for a later epoch. The pass logs
  `Submissions feed: <n> row(s), <m> valid, <k> to verify`, where `<k>` counts articles.
- An article's candidates go through every check (§8.2) in that order, and the first that passes is
  credited: the earliest matching draft wins, and an earlier draft that fails any check does not
  block a later one. The remaining candidates are not credited. Each article's page is fetched at
  most once per pass, however many candidates it has.
- The uploaded text is used only for verification. The validator never publishes, stores or logs
  it.
- A feed that cannot be read (unset endpoint, HTTP or JSON error, or a body that is not a list)
  burns the day: `Submissions feed read failed: <error>`, then
  `INCENTIVE_BURN epoch=<e> reason=feed_unavailable`.

### 8.2 What each article must pass

`verify_article()` runs these checks in order and stops at the first failure. Each candidate tried
logs `SUBMISSION_RESULT <submission_id> <reason>`, and the one credited then logs
`SUBMISSION_CREDITED <submission_id> candidate=<i>/<n>`:

| Reason | Check |
|---|---|
| `brief_not_active` | The row's brief is not in the signed active brief feed. |
| `outlet_not_listed` | The link's outlet is not in the signed outlet registry. |
| `outlet_not_supported` | The outlet's fetch strategy is not `direct` or `proxy`; every content check runs on the page this validator fetches itself. |
| `url_not_live` | The page could not be fetched as a live article. |
| `publication_date_unverifiable` | The page gives no publication time. |
| `published_outside_window` | Published after chain time or more than `HERALD_MAX_ARTICLE_AGE_DAYS` before it; or, for a brief with an end date, outside start date minus `HERALD_PUBLISH_BUFFER_DAYS` (00:00 UTC) through end date (23:59:59 UTC). Standing briefs have no brief window. |
| `published_before_upload` | The page states an exact publication time (a time of day with `Z` or an explicit UTC offset) earlier than the upload. Otherwise, the UTC publication day is earlier than the UTC day of the upload: a date alone, a time with no offset, or exactly 00:00:00 in the stated offset (how many sites render a date alone) is not exact. |
| `draft_mismatch` | Less than `HERALD_DRAFT_MATCH_THRESHOLD` of the uploaded text's five-word shingles (lower-cased, punctuation removed) appear in the fetched article body. |
| `paid_not_real_news` | Generic or outlet-specific paid-content rules match. |
| `topic_mismatch` | The article does not match the brief's topic (§6). |
| `verify_error` | Verifying this candidate raised an error; only this candidate is rejected. |
| `ok` | Passed. |

A passing article is valued at `HERALD_BASE_PAYOUT_USD` × tier multiplier × search factor (1.0 when
the search index has it, `HERALD_NO_SEARCH_FLOOR` otherwise) and starts a vesting entry on the
incentive hotkey that releases over `HERALD_VEST_EPOCHS` daily installments while the article stays
live. After `HERALD_DEAD_CONFIRM_EPOCHS` consecutive confirmed-dead epochs the remaining
installments are clawed back; there is no slashing.

### 8.3 Settings

| Setting | Default | Meaning |
|---|---|---|
| `HERALD_INCENTIVE_HOTKEY` | none | The SS58 hotkey every verified article vests to, and the only UID besides 0 that receives weight. Provided by the subnet operator; identical on every validator. Preflight requires a valid SS58 address that differs from `HERALD_REGISTRY_AUTHORITY_HOTKEY`. |
| `HERALD_DRAFT_MATCH_THRESHOLD` | `0.6` | Share of the uploaded text that must appear in the published article. |
| `HERALD_PUBLISH_BUFFER_DAYS` | `3` | Days before a brief's start date from which publication counts. |
| `HERALD_MAX_ARTICLE_AGE_DAYS` | `21` | Oldest accepted publication, counted back from the scoring block's chain time. |
| `HERALD_MAX_SUBMISSIONS_PER_EPOCH` | `500` | New articles verified per epoch, earliest upload first. |
| `HERALD_MAX_CANDIDATES_PER_ARTICLE` | `10` | Submissions of one article tried per epoch, earliest upload first. |
| `HERALD_WEIGHT_RESUBMIT_BLOCKS` | `180` | Blocks the chain's weight record for this validator's uid must reach before the latest weights are submitted again (§8.5). |

All but `HERALD_WEIGHT_RESUBMIT_BLOCKS` are in the consensus fingerprint. Change them only
fleet-wide, together with `HERALD_EXPECTED_CONSENSUS_FP` on every validator and the backend (§3).
`HERALD_WEIGHT_RESUBMIT_BLOCKS` sets only how often weights are submitted, not what they are, so it
is not in the fingerprint and may differ between validators.

### 8.4 Pricing the day's miner emission

Before it reads the registry or the feed, the validator prices one day of miner emission at the
scoring block:

```text
daily_miner_alpha = BLOCKS_PER_DAY × alpha_out × MINER_EMISSION_SHARE × mechanism_ratio
daily_usd         = daily_miner_alpha × alpha_tao × tao_usd
```

| Input | Source |
|---|---|
| `alpha_out` | The subnet's per-block alpha emission (`alpha_out_emission`) from its dynamic info at the scoring block. |
| `mechanism_ratio` | Mechanism 0's share of the subnet's mechanism emission split at the scoring block; an even split across mechanisms when none is set. |
| `alpha_tao` | The subnet's alpha price in TAO at the scoring block. |
| `tao_usd` | CoinGecko's public simple-price API (`bittensor` in USD): up to 3 attempts, 10 s timeout each, no API key. |
| `BLOCKS_PER_DAY` | `7200` (one evaluation epoch). |
| `MINER_EMISSION_SHARE` | `0.41`, the share of each block's alpha emission that goes to miners. |

The constants and the price source name `chain_spot_alpha_x_coingecko_tao_usd_v1` are set in
`herald/validator/news/pricing.py`, not in the environment, and are part of the fingerprint. An
input that cannot be read, or is not a finite positive number, burns the day with
`reason=pricing_error (<detail>)`.

### 8.5 Weights and the burn

Each scoring pass sums the installments released by vesting articles, caps client briefs at their
prepaid reward pools, and sets:

```text
w       = min(1, payable_usd / daily_usd)
weights = {incentive hotkey UID: w, UID 0: 1 − w}      (zero entries dropped)
```

Weight on UID 0 is burned. With nothing payable, all weight goes to UID 0 and the pass still
publishes and logs `INCENTIVE_WEIGHT … w=0.000000`.

All weight goes to UID 0 for the day, logged as `INCENTIVE_BURN epoch=<e> reason=<reason>`, when:

| Reason | Cause |
|---|---|
| `no_briefs` | The signed brief feed verified as empty. |
| `incentive_hotkey_unset` | `HERALD_INCENTIVE_HOTKEY` is empty. |
| `incentive_hotkey_not_registered` | The incentive hotkey is not in the metagraph. |
| `incentive_hotkey_is_validator_hotkey` | The incentive hotkey is this validator's own wallet hotkey. |
| `incentive_hotkey_at_burn_uid` | The incentive hotkey holds UID 0. |
| `chain_time_unavailable` | The scoring block's timestamp could not be read. |
| `pricing_error (<detail>)` | A pricing input failed (§8.4). |
| `feed_unavailable` | The submissions feed could not be read (§8.1). |
| `error (<type>: <detail>)` | Any other error in the shared steps, for example loading the outlet registry or its on-chain anchor. |
| `stale_scores` | An already scored epoch holds scores on a UID other than 0 and the incentive hotkey's current UID (scores from an earlier release, or the incentive hotkey moved to another UID). The scores are replaced with the burn, and the next weight submission sends the burn in their place. |

For every reason except `no_briefs` and `stale_scores`, the pass has failed: every ledger change it
made is discarded, nothing is published, and the epoch is marked scored so it is not retried.
Installments not released that day are caught up by the next successful epoch. A failure before the
epoch is known (the ledger or the chain head cannot be read) logs `Error in Herald forward pass`
and changes nothing.

**Submitting and re-submitting weights.** Scoring runs once per epoch, but the chain's copy of a
validator's weights ages: once its last update is older than the subnet's activity cutoff (5,000
blocks on netuid 69), the chain stops counting that validator's weights. So once an epoch has been
scored or burned, the validator submits the latest vector, incentive and burn or burn only, whenever
the chain's weight record for its own uid (`LastUpdate`) is at least `HERALD_WEIGHT_RESUBMIT_BLOCKS`
blocks old (default 180). The stored vector changes only when an epoch is scored or burned, or its
scores are replaced by the burn (`stale_scores`), and a new vector is submitted under the same rule.
At each weight-setting step:

- the `--neuron.epoch_length` interval (default 100 blocks) and `--neuron.disable_set_weights`
  still apply first;
- nothing is submitted before the first scored epoch or while the scores are empty;
- a record younger than the interval logs
  `Weights for uid <uid> are <n> blocks old (< <interval>); skipping resubmission`;
- a record whose age cannot be read logs `Unable to read the age of this uid's weight record: …` at
  WARNING and nothing is submitted;
- a commit of this hotkey still waiting for its automatic reveal logs
  `Weight commitment pending automatic reveal; skipping resubmission`;
- otherwise the validator logs `Submitting Herald epoch <e> weights: …` (the first submission of
  that epoch's vector) or `Re-submitting Herald epoch <e> weights: …` (a later one), followed by the
  checks of §8.6, which apply to every submission.

With commit-reveal, a commit is revealed at the next tempo boundary, and while it waits no new
commit is sent. On netuid 69 (tempo 360) the record is therefore refreshed about once per tempo,
roughly every 72 minutes, whatever the interval is set to, well inside the activity cutoff. Each
accepted submission records the epoch as submitted and, with `HERALD_RESULTS_ENDPOINT` set, sends a
signed weight receipt; that record is bookkeeping and does not decide when the next submission
happens.

### 8.6 Weight vector checks and MinAllowedWeights

At every submission, including each re-submission, `set_weights` builds the u16 vector straight
from the scores. It is never padded with other registered UIDs and never clipped to
`MaxWeightsLimit`.

- Scores on any UID other than 0 and the incentive hotkey's current UID turn the vector into all
  weight on UID 0: `WEIGHT_VECTOR_BURN reason=incentive_uid_changed: …` at WARNING.
- Before submitting, the validator logs `WEIGHT_VECTOR uids=[…] weights=[…]` at INFO.
- The vector is refused, with `WEIGHT_VECTOR_REFUSED reason=<reason>` at ERROR and no extrinsic
  sent, when:
  - `empty_vector` — no entry is left after conversion;
  - `uid_not_allowed uids=[…] allowed=[…]` — it reaches a UID other than 0 and the incentive UID;
  - `min_allowed_weights_unknown` — the subnet's MinAllowedWeights could not be determined;
  - `below_min_allowed_weights uids=[…] min_allowed_weights=<n>` — it has fewer entries than
    MinAllowedWeights;
  - `read_error (<detail>)` — reading MinAllowedWeights or building the vector failed.

  Nothing is marked as submitted, and the validator tries again at its next weight-setting step
  while the chain's record stays old.

**MinAllowedWeights must be 1.** A burn-only vector has a single entry (UID 0), so on a subnet whose
MinAllowedWeights is above 1 every burn-only vector is refused and no weights are set. Check
`min_allowed_weights` with `btcli subnet hyperparameters --netuid 69 --network finney`.

### 8.7 Release 0.2.0 (spec version 20)

- `herald/__init__.py` is `0.2.0`, so the spec version, sent as `version_key` with every weight
  submission, is `20`.
- A score checkpoint (`state.npz`) written by another spec version is discarded at startup
  (`Discarding score checkpoint from spec <n>; current spec is 20`).
- The consensus fingerprint changes with this release. Set `HERALD_INCENTIVE_HOTKEY`, recompute the
  fingerprint and set `HERALD_EXPECTED_CONSENSUS_FP` on every validator and the backend, then
  recreate the whole fleet together.
- `herald_state.json` keeps its format (schema 2). Vesting entries started by an earlier release
  carry no submission id; the first successful scoring pass expires them and logs
  `LEGACY_VESTING_EXPIRED <n>`.

### 8.8 Log tags

| Line | Level | When |
|---|---|---|
| `OWNER_VALIDATOR_CHECK wallet_hotkey_is_uid0=<True\|False>` | INFO | At startup: whether this validator's wallet hotkey is the hotkey registered at UID 0. `OWNER_VALIDATOR_CHECK unavailable: <error>` at WARNING if it cannot be read. |
| `SUBMISSION_RESULT <submission_id> <reason>` | INFO | Once per candidate tried (§8.1, §8.2). |
| `SUBMISSION_CREDITED <submission_id> candidate=<i>/<n>` | INFO | The candidate credited with its article: the `i`-th of the article's `n` candidates in upload order. |
| `LEGACY_VESTING_EXPIRED <n>` | INFO | A pass expired `n` vesting entries that carry no submission id. |
| `INCENTIVE_WEIGHT epoch=<e> w=<w> payable_usd=<usd> daily_usd=<usd> alpha_tao=<price> tao_usd=<price> daily_miner_alpha=<alpha> uid_star=<uid>` | INFO | A successful scoring pass; `uid_star` is the incentive hotkey's UID. |
| `INCENTIVE_BURN epoch=<e> reason=<reason>` | INFO for `no_briefs` and `stale_scores`, WARNING otherwise | The day's weight went to UID 0 (§8.5). |
| `Submitting Herald epoch <e> weights: the chain record for uid <uid> is <n> blocks old (>= <interval>)` | INFO | The first submission of epoch `e`'s vector passed every submission gate (§8.5). |
| `Re-submitting Herald epoch <e> weights: the chain record for uid <uid> is <n> blocks old (>= <interval>)` | INFO | The same vector is submitted again because the chain's record reached the interval (§8.5). |
| `Weights for uid <uid> are <n> blocks old (< <interval>); skipping resubmission` | INFO | The chain's record is still fresh; nothing is submitted. |
| `Weight commitment pending automatic reveal; skipping resubmission` | INFO | A commit of this hotkey awaits its reveal; nothing is submitted. |
| `No Herald epoch has been scored yet; skipping weight submission` | INFO | No vector exists yet. |
| `Unable to read the age of this uid's weight record: <error>` | WARNING | The record's age could not be read; nothing is submitted. |
| `WEIGHT_VECTOR uids=[…] weights=[…]` | INFO | The u16 vector about to be submitted. |
| `WEIGHT_VECTOR_BURN reason=incentive_uid_changed: …` | WARNING | Scores outside UID 0 and the incentive UID were replaced by the burn at submission. |
| `WEIGHT_VECTOR_REFUSED reason=<reason>` | ERROR | The vector was refused and no extrinsic was sent (§8.6). |

### 8.9 Feed health from outside

To check the backend's feed health from outside, add `--check-board-feed` to the watchdog, run from
a host checkout with the §4.1 environment:

```bash
python scripts/watchdog.py --hotkey <validator ss58> --check-board-feed
```

It adds a `board_feed` line read from the backend's public `GET /public/placements/feed-health`
(aggregate counts and alarm codes only, sent without a credential; the watchdog only sends GET
requests). Any alarm the backend reports is a breach and exits 1. `feed_not_read` means no feed read
was recorded recently while submissions are waiting: check your endpoint and read credential. It
counts reads by every validator, so a quiet feed check does not prove your own read works; your
validator's `Submissions feed: … row(s)` line does. The other codes the watchdog recognises
(`submitted_not_settled`, `signer_stale`, `pool_hotkey_unregistered`, `committed_not_submitted`,
`settlement_mismatch`, `expired_claim_vesting`) are for the subnet operator, but they exit 1 as
well: if a job pages you on the exit code, leave the flag out of it, or you will be paged for
conditions only the operator can fix. An unrecognised code is also a breach. An unreachable
endpoint or an unexpected payload exits 2. Without the flag the watchdog runs exactly as before.

---

## 9. Troubleshooting (real gotchas)

- **`ModuleNotFoundError: No module named 'core'`** — the Dockerfile `pip install -e .` runs before
  `core/`/`neurons/` are copied, so they aren't registered. Set `PYTHONPATH=/app` (already in the
  provided compose).
- **Startup hangs silently** — with a TTY and no `WANDB_API_KEY`, `wandb.init()` blocks on a login
  prompt. Set `WANDB_MODE=disabled` (or provide a key).
- **Runs but never publishes a snapshot** — scoring only fires when `self.step % VALIDATOR_STEPS_INTERVAL == 0`
  (default 240 ≈ 4 h), and **`self.step` persists across restarts** (`state.npz`). Frequent restarts
  march the counter past the scoring step. Lower `HERALD_VALIDATOR_STEPS_INTERVAL` (e.g. 1–10) to
  poll often; actual scoring stays gated to once per epoch and is **not** in the fingerprint. A day
  that was burned publishes nothing either: look for `INCENTIVE_BURN` and its reason (§8.5).
- **`INCENTIVE_BURN … reason=incentive_hotkey_…`** — check that `HERALD_INCENTIVE_HOTKEY` is the
  operator's value and is registered on the subnet, and that the validator is not running with that
  hotkey as its own wallet hotkey.
- **`WEIGHT_VECTOR_REFUSED reason=below_min_allowed_weights …`** — the subnet's MinAllowedWeights is
  above the number of entries in the vector; it must be 1 (§8.6).
- **Can't see what it's doing** — bittensor logs at WARNING unless told otherwise. The provided
  compose starts the validator with `--logging.info` (set `HERALD_VALIDATOR_LOGGING=--logging.debug`
  for more, in the shell or the compose project's `.env`: compose substitutes it into the command
  and never reads it from a `VALIDATOR_ENV_FILE`). Env and command changes apply when the container
  is recreated with `docker compose --profile validator up -d validator` (add `--build` after
  pulling new code, and prefix `VALIDATOR_ENV_FILE=…` if you deploy with one): `docker restart`
  keeps the command, environment and image the container was created with. A hand-written run
  command needs the flag itself to show `step(N)` / `Herald forward pass` / snapshot lines.
- **`Refusing to start: Herald state file …`** — `herald_state.json` may hold in-flight placements,
  so the validator will not continue without it. With the provided compose the file lives in the
  `validator_state` volume at
  `/root/.bittensor/miners/<WALLET_NAME>/<HOTKEY_NAME>/netuid69/validator/herald_state.json` (the
  validator prints that directory as `full path:` at startup). The container restarts in a loop while
  it refuses, so stop it before touching the file (prefix each compose command with
  `VALIDATOR_ENV_FILE=…` if you deploy with one):

  ```bash
  docker compose --profile validator stop validator
  docker compose --profile validator run --rm --no-deps --entrypoint sh validator
  #   cd /root/.bittensor/miners/<WALLET_NAME>/<HOTKEY_NAME>/netuid69/validator
  #   ls -l herald_state.json*          # dated .bak files, newest last
  #   cp -p herald_state.json.bak.<newest UTC timestamp> herald_state.json
  #   exit
  docker compose --profile validator up -d validator
  ```

  The message says which case applies:
  - *exists but cannot be loaded*, or *is missing but backups of it exist* — restore the newest
    `herald_state.json.bak.<UTC timestamp>` as above.
  - *needs a validator that reads schema N or later* — a newer release changed the file's format
    (typically seen after a rollback). Run that release again. Only if you must stay rolled back,
    set `HERALD_STATE_ALLOW_NEWER_SCHEMA=true` and recreate the container: the file is copied aside
    as `herald_state.json.schema<N>.<UTC timestamp>`, and what this release does not understand is
    dropped on its next save. A newer file that only adds fields loads without the override, after
    the same copy.

  Only if no usable backup exists, set `HERALD_STATE_ALLOW_FRESH_ON_CORRUPT=true` and recreate the
  container: an unreadable file is copied aside as `herald_state.json.corrupt.<UTC timestamp>`, a
  missing file's backups are renamed `….kept` so rotation never deletes them, and the ledger starts
  empty. Keep the override set until the validator has saved a new state (`herald_state.json`'s
  modification time changes after its next scoring pass, about once a day): an unreadable file is
  only copied aside, so unsetting the override earlier makes the next start refuse again. While
  either override is set the validator logs a WARNING on every start; once the save has happened,
  unset it and recreate the container.
- **`WEIGHT_SUBMISSION_STALLED`** — the validator had weights to submit, but the pending
  weight-commit check kept failing, so it is deliberately not setting weights (it will not risk a
  duplicate commit). Set `HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT` to a second finney node and recreate
  the container; it is used only for this check. Keep `SUBTENSOR_NETWORK=finney`, as production
  preflight requires. The check's own log lines show endpoints as `scheme://host[:port]` only. Watch
  liveness from outside with `python scripts/watchdog.py --hotkey <validator ss58>`, run from a host
  checkout with the §4.1 environment (the validator image does not contain `scripts/`): it checks
  the on-chain LastUpdate age and the backend's latest snapshot epoch, and exits 1 on a breach, 2
  when a check cannot run. Add `--check-board-feed` to check the backend's feed health as well
  (§8.9).
- **Occasional `UnknownBlock: Expect block number from id`** — transient inconsistency from the
  shared public finney endpoint (a load-balanced pool). The neuron retries and recovers; if it's
  *frequent* in the weight-commit check, set `HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT` as above.
  (Consistent failures are almost never the endpoint — check the step cadence above first.)
- **`403` publishing snapshots** — either your hotkey isn't enrolled/chain-verified as a reporter
  yet (the operator must `POST /admin/reporters` and the finalized-chain indexer must confirm your
  permit), or `HERALD_RESULTS_WRITE_TOKEN` is a write token the operator bound to a different
  hotkey. The validator logs only the status line, so ask the operator which it is.
- **`401` on reports or the feed** — the token is wrong or was retired, or the read and write tokens
  are swapped (a read token is refused on reports, a write token on the feed). A failed feed read
  logs `Submissions feed read failed: …` at WARNING and burns the day
  (`INCENTIVE_BURN … reason=feed_unavailable`). The feed is read only in epochs with active briefs,
  after the incentive hotkey check and pricing succeed.

See also: [miner.md](miner.md) for how contributors submit the articles you verify.
