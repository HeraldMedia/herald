# Herald Validator Guide (Bittensor netuid 69 · finney)

A Herald **validator** runs the code-only **verification oracle**: it pulls open briefs from the
subnet backend, reads the articles contributors submitted through the Herald website, checks that
each submission is signed by the coldkey that owns the contributor's **own hotkey**, verifies the
article against the outlet's own page, vests its value on that hotkey, sets weights on the
contributors' UIDs (and on UID 0, which burns the share verified value does not cover), and
publishes signed epoch snapshots. **No GPU / no ML** — it is network-I/O bound (web fetches,
search-API calls, chain RPC, one price API). The optional LLM judge is a *remote* API.

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
| Network | — | outbound only (no inbound port), ≥100 Mbps, generous egress |
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
- **Built into the release** for finney netuid 69 (`herald/network_profile.py`, §8.3). These are
  public, fleet-wide values; you set none of them:
  - Backend endpoint (canonical): `https://api.heraldmedia.ai`
  - Brief-feed pubkey: `a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a`
  - Registry pubkey: `9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6`
  - Registry **authority hotkey**: `5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5`
  - Epoch alignment: `HERALD_EPOCH_LAG=-12803`
- **From the subnet operator:**
  - **Results credentials** — the shared results token, or a per-validator write and read token
    pair (secret). The read credential is what reads the submissions feed.
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
the per-article candidate cap), the emission mode, the submission intake version and the pricing
constants (§8), and the trust anchors (pubkeys, authority hotkey). Release `0.2.1` changes the
fingerprint (§8.7).

Derive it and pin it on the validator **and** give it to the backend operator
(`HERALD_EXPECTED_CONSENSUS_FP` on both):
```bash
# Docker (loads .env, no wallet needed):
docker compose --profile validator run --rm --no-deps --entrypoint python \
  validator -m herald.production fingerprint --netuid 69 --subtensor.network finney
# or bare-metal:
set -a; source .env; set +a
python -m herald.production fingerprint --netuid 69 --subtensor.network finney
```
The flags apply the release's built-in mainnet values (§8.3) exactly as the validator does, so the
printed value is the one it computes at startup. A release that changes a consensus value changes
the fingerprint: recompute it after every pull.
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
shown below. The public, fleet-wide values (backend endpoints, pubkeys, the authority hotkey, the
epoch alignment) are built into the release and stay commented out; you supply your wallet, your
API keys and the operator-provided results credential:
```ini
# Herald validator — production .env (Bittensor netuid 69, finney).
# Copy to .env, fill the blanks (your wallet, API keys and results credential), then: chmod 600 .env
# For every available setting (incl. the consensus-critical scoring tunables), see root .env.example.
HERALD_PRODUCTION=true
HERALD_PRODUCTION_NETUID=69
NETUID=69
SUBTENSOR_NETWORK=finney
# The provided compose connects with --subtensor.network (finney, as production preflight requires)
# and passes no chain endpoint. HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT (below) adds a second finney
# node that is used only for the pending weight-commit check.

# ── Wallet: registered on netuid 69 with stake for a validator permit ──
WALLET_NAME=
HOTKEY_NAME=
# No axon settings: a validator serves nothing and announces no address.

# ── Built into the release for finney netuid 69 (herald/network_profile.py) ──
# The canonical backend, the trust anchors and the epoch alignment are public, fleet-wide values
# that each release carries: pulling a new release and recreating the container picks them up.
# Leave them unset. A non-empty value here overrides the release's value and pins this validator
# to it, so set one only to deliberately deviate (for example on a test network).
# HERALD_RESULTS_ENDPOINT=https://api.heraldmedia.ai
# HERALD_BRIEFS_ENDPOINT=https://api.heraldmedia.ai/api/v2/validator/briefs
# HERALD_REGISTRY_ENDPOINT=https://api.heraldmedia.ai
# HERALD_BRIEFS_PUBKEY=a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a
# HERALD_REQUIRE_SIGNED_BRIEFS=true
# HERALD_REGISTRY_PUBKEY=9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6
# HERALD_REGISTRY_AUTHORITY_HOTKEY=5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5
# HERALD_REQUIRE_SIGNED_REGISTRY=true
# HERALD_EPOCH_LAG=-12803
# Retired in 0.2.1: each contributor's own hotkey is paid, so HERALD_INCENTIVE_HOTKEY and
# HERALD_BURN_UNEARNED do nothing. Do not set them; startup logs a WARNING while either is set.

# ── Results credential (operator-provided, secret) ──
# The shared token validators present to the backend's results routes:
HERALD_RESULTS_TOKEN=
# Or scoped credentials, once the operator issues them: the write token for reports and the read
# token for the submissions feed. Each one set replaces the shared token for its routes.
# HERALD_RESULTS_WRITE_TOKEN=
# HERALD_RESULTS_READ_TOKEN=

# ── Signed outlet registry file (the production preflight verifies it at startup) ──
# Scoring reads the active edition from the backend and falls back to this file.
# Docker:      HERALD_REGISTRY_HOST_FILE is the host file; compose bind-mounts it read-only at
#              HERALD_REGISTRY_PATH inside the container.
# Bare-metal:  set HERALD_REGISTRY_PATH to the host file directly and leave HOST_FILE unset.
HERALD_REGISTRY_HOST_FILE=/secure/herald/outlets.signed.json
HERALD_REGISTRY_PATH=/run/registry/outlets.signed.json

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
# Compute with `python -m herald.production fingerprint --netuid 69 --subtensor.network finney` (the
# flags apply the release's mainnet values, as the validator does); set the SAME value here AND on
# the backend. It changes when a release changes a consensus value, so recompute it after a pull.
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
non-localhost; a results write and read credential (scoped, or the shared token); **ScrapingBee
key present** if the registry has `proxy:` outlets (and an NYT key if it has `api:nyt`); **at least
one search provider**; `HERALD_EXPECTED_CONSENSUS_FP` set and matching the computed fingerprint; and
a **live on-chain `HRLDREG` registry anchor** exists. Any failure prints
`production preflight failed: <exact reasons>`.

On finney netuid 69 the release fills the endpoints, pubkeys, signing requirements and the
authority hotkey when `.env` leaves them unset (§8.3), so those checks pass without setting them.

Preflight sees only the environment. Contributors' hotkeys, their owners and their UIDs are read
from the chain at every scoring pass instead (§8.1, §8.2). The retired `HERALD_INCENTIVE_HOTKEY` and
`HERALD_BURN_UNEARNED` are not checked (§8.7).

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
2. Recompute the fingerprint (`python -m herald.production fingerprint --netuid 69 --subtensor.network finney`) and set the new value on
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
confirmed epochs are the backend's canonical record of each epoch's results.

- **One confirming operator:** set `HERALD_QUORUM_REQUIRED=1` on the backend when a single
  operator's validator is the only enrolled reporter; a higher value cannot be met by one reporter.
- **Independent validators:** raise it to 2 or more once independent validators are enrolled, so
  two validators corroborate each epoch before its results are canonical.

Snapshots are `schema_version` 2. The state holds the articles (each with the hotkey it vests to),
the briefs' pools, each miner UID's payable USD with its hotkey (`rewards`), the u16 weights by UID
and hotkey (`weights`), and the emission mode, `emission: miner_hotkeys_v1` (§8.5). An epoch whose
scoring failed publishes no results or snapshot (§8.5).

---

## 8. Contributor submissions, miner hotkeys and weights

Contributors (PR firms and PR professionals) take part through the Herald website with Google
sign-in. Each registers their own hotkey on netuid 69 from the website, signing with a wallet
extension (Talisman, SubWallet or Polkadot.js); they run no miner server or axon. For a submission
they pick a brief, upload the text they will publish before publishing, sign the submission with
the coldkey that owns their hotkey, then add the published link. The validator checks the signature
and, on chain, the hotkey's owner; verifies the article itself; vests its value on the
contributor's hotkey; and sets weight on the contributors' UIDs and UID 0 only. It sends no queries
to miners.

### 8.1 Submissions feed and read credential

- Each scoring pass reads
  `GET {HERALD_RESULTS_ENDPOINT}/api/v4/validator/submissions?network=<network>&netuid=<netuid>`
  with the read credential, `HERALD_RESULTS_READ_TOKEN` or the shared `HERALD_RESULTS_TOKEN`
  (10 s timeout). It is read after the brief feed, the chain time, pricing and the outlet registry.
  Production preflight requires `HERALD_RESULTS_ENDPOINT` and a read credential.
- Each row carries `submission_id`, `network`, `netuid`, `brief_id`, `url`, `draft_text` (the
  uploaded text), `uploaded_ts` (unix seconds, UTC), `hotkey` (the contributor's hotkey),
  `coldkey` and `signature`. Of the first 10,000 rows, a row is used only when:
  - `network` and `netuid` are this validator's;
  - `submission_id` and `brief_id` are 1–128 characters of `A–Z a–z 0–9 . _ : -`;
  - `url` is an ASCII `https` URL of at most 2,048 characters with no query string once tracking
    parameters are removed;
  - `draft_text` is 300–40,000 characters after trimming surrounding whitespace;
  - `uploaded_ts` is a positive integer no later than the scoring block's chain time;
  - `hotkey` and `coldkey` are SS58 addresses, and `signature` (`0x` and 128 hex digits) is
    `coldkey`'s signature over the submission message below. A wallet signature of the message
    wrapped in `<Bytes>…</Bytes>` verifies too.
- The submission message is UTF-8, its lines joined by `\n` with no trailing newline
  (`herald/validator/news/signatures.py`):
  ```text
  Herald submission v1
  network: <network>
  netuid: <netuid>
  brief: <brief_id>
  draft: <sha256 hex of draft_text, UTF-8>
  hotkey: <hotkey SS58>
  ```
  The validator rebuilds it from the row and hashes `draft_text` itself, so the backend can neither
  move a submission to another hotkey nor change the text the contributor signed for.
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
- Whether `coldkey` owns `hotkey` is read from the chain (`SubtensorModule.Owner`) at the scoring
  block, once per distinct hotkey among the selected articles' candidates. A candidate whose
  `coldkey` is not the owner is skipped before any other check, with
  `SUBMISSION_RESULT <submission_id> hotkey_not_owned`, and the article's next candidate is tried.
  An owner read that fails fails the day (§8.5).
- The uploaded text is used only for verification. The validator never publishes, stores or logs
  it.
- A feed that cannot be read (unset endpoint, HTTP or JSON error, or a body that is not a list)
  fails the day (§8.5): `Submissions feed read failed: <error>`, then
  `EPOCH_BURN epoch=<e> reason=feed_unavailable`.

### 8.2 What each article must pass

After the ownership check, `verify_article()` runs these checks in order and stops at the first
failure. Each candidate tried logs `SUBMISSION_RESULT <submission_id> <reason>`, and the one
credited then logs `SUBMISSION_CREDITED <submission_id> candidate=<i>/<n> hotkey=<ss58>`:

| Reason | Check |
|---|---|
| `hotkey_not_owned` | The row's `coldkey` does not own its `hotkey` on chain at the scoring block (§8.1). Checked first. |
| `brief_not_active` | The row's brief is not in the signed active brief feed. |
| `outlet_not_listed` | The link's outlet is not in the signed outlet registry. |
| `outlet_not_supported` | The outlet's fetch strategy is not `direct` or `proxy`; every content check runs on the page this validator fetches itself. |
| `url_not_live` | The page could not be fetched as a live article. |
| `publication_date_unverifiable` | The page gives no publication time. |
| `published_outside_window` | Published after chain time or more than `HERALD_MAX_ARTICLE_AGE_DAYS` before it; or, for a brief with an end date, outside start date minus `HERALD_PUBLISH_BUFFER_DAYS` (00:00 UTC) through end date (23:59:59 UTC); a publication time that is not exact (next row) counts on the date the outlet states, from start date minus the buffer through end date. Standing briefs have no brief window. |
| `published_before_upload` | The page states an exact publication time (a time of day with `Z` or an explicit UTC offset) earlier than the upload. Otherwise, the publication date as the outlet states it is earlier than the UTC day of the upload: a date alone, a time with no offset, or exactly 00:00:00 in the stated offset (how many sites render a date alone) is not exact, and is judged by the date the outlet states, in its own offset (`2026-09-08T00:00:00+02:00` is 8 September). |
| `draft_mismatch` | Less than `HERALD_DRAFT_MATCH_THRESHOLD` of the uploaded text's five-word shingles (lower-cased, punctuation removed) appear in the fetched article body. |
| `paid_not_real_news` | Generic or outlet-specific paid-content rules match. |
| `topic_mismatch` | The article does not match the brief's topic (§6). |
| `verify_error` | Verifying this candidate raised an error; only this candidate is rejected. |
| `ok` | Passed. |

A passing article is valued at `HERALD_BASE_PAYOUT_USD` × tier multiplier × search factor (1.0 when
the search index has it, `HERALD_NO_SEARCH_FLOOR` otherwise) and starts a vesting entry on the
contributor's hotkey (the row's `hotkey`), recording its submission id and signing coldkey. The
entry releases over `HERALD_VEST_EPOCHS` daily installments while the article stays live. After
`HERALD_DEAD_CONFIRM_EPOCHS` consecutive confirmed-dead epochs the remaining installments are clawed
back; there is no slashing.

**Unregistered hotkeys.** Every pass first resyncs the metagraph, so registrations are read as they
stand when the epoch is scored rather than at the last periodic sync. It then looks up each vesting
article's hotkey and pays whichever UID it holds then. While the hotkey is not registered (for example after it was
deregistered), a live article holds: nothing is released, and the pass logs
`VESTING_HELD_UNREGISTERED <n>`. When the hotkey registers again, the installments it missed are
released together. An article more than `HERALD_VEST_EPOCHS + HERALD_VEST_GRACE_EPOCHS` epochs
after its start expires with whatever it has not released.

### 8.3 Settings

| Setting | Default | Meaning |
|---|---|---|
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

`HERALD_INCENTIVE_HOTKEY` and `HERALD_BURN_UNEARNED` are retired (§8.7): they do nothing and are not
in the fingerprint. While either is set, startup logs
`<name> is set but no longer used: each miner's own hotkey is paid; remove it` at WARNING.

**Built-in mainnet values.** A validator started with `--netuid 69` on finney (the provided compose
file and `scripts/run_validator.sh` both pass these flags) takes the values below for every setting
its environment leaves unset or empty. They live in `herald/network_profile.py`, so each release
carries the current ones and a pull plus a recreate picks them up. A non-empty value in `.env`
always wins and pins the validator to it, so leave these unset unless you mean to deviate. Startup
logs `MAINNET_DEFAULTS_APPLIED: <names>` with the settings the release filled.

| Setting | Mainnet value |
|---|---|
| `HERALD_EPOCH_LAG` | `-12803` (epoch 1258 began at block 9044797) |
| `HERALD_RESULTS_ENDPOINT`, `HERALD_REGISTRY_ENDPOINT` | `https://api.heraldmedia.ai` |
| `HERALD_BRIEFS_ENDPOINT` | `https://api.heraldmedia.ai/api/v2/validator/briefs` |
| `HERALD_BRIEFS_PUBKEY` | `a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a` |
| `HERALD_REGISTRY_PUBKEY` | `9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6` |
| `HERALD_REGISTRY_AUTHORITY_HOTKEY` | `5FWB5CFZQB4FcmekEXrXtoGgjFt37HGQk27JzWKkRzqWjkg5` |
| `HERALD_REQUIRE_SIGNED_BRIEFS`, `HERALD_REQUIRE_SIGNED_REGISTRY` | `true` |

On any other network or netuid nothing is filled.

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
input that cannot be read, or is not a finite positive number, fails the day (§8.5) with
`reason=pricing_error (<detail>)`.

### 8.5 Weights and the burn

Each scoring pass sums, for each miner UID, the installments its vesting articles release, and caps
client briefs at their prepaid reward pools (a brief short of funds pays each UID the same fraction
of its installments). With `usd` a UID's payable USD, `U` the total over every miner UID and
`d = daily_usd` (§8.4), the vector (`miner_weight_vector()` in `emission.py`) is:

```text
U <= d:  each miner UID  usd / d;   UID 0  1 − U / d   (burned)
U >  d:  each miner UID  usd / U;   UID 0  nothing
```

Zero entries are dropped. With nothing payable, all weight goes to UID 0, and the pass still
publishes and logs `EPOCH_WEIGHTS … miners=0 … burn=1.000000`. A miner is paid only through the
hotkey its contributor signed for, on the UID that hotkey holds when the epoch is scored (§8.2).
The pass records those UIDs with their hotkeys (`weight_hotkeys` in `herald_state.json`); only they
and UID 0 may receive the epoch's weight (§8.6). The snapshot states each miner UID's payable USD
and hotkey (`rewards`) and the u16 weights, all part of the signed state and its `state_hash` (§7).

A pass that does not score the day puts all of its weight on UID 0 and logs
`EPOCH_BURN epoch=<e> reason=<reason>`. The reasons:

| Reason | Cause |
|---|---|
| `no_briefs` | The signed brief feed verified as empty. |
| `chain_time_unavailable` | The scoring block's timestamp could not be read. |
| `pricing_error (<detail>)` | A pricing input failed (§8.4). |
| `feed_unavailable` | The submissions feed could not be read (§8.1). |
| `metagraph_unavailable` | The metagraph could not be resynced before scoring (§8.2). |
| `error (<type>: <detail>)` | Any other error in the shared steps, for example loading the outlet registry or its on-chain anchor, or reading hotkey owners from the chain. |
| `stale_scores` | An already scored epoch's stored scores are empty (for example after a score checkpoint from another spec version was discarded at startup) or reach a UID the epoch did not score. They are replaced with all weight on UID 0 for the rest of the epoch, and the next weight submission sends the replacement. The ledger is not touched. |

For every reason except `no_briefs` and `stale_scores`, the pass has failed: every ledger change it
made is discarded, nothing is published, and the epoch is marked scored so it is not retried.
Installments not released on a burned day are caught up by the next successful epoch. A failure
before the epoch is known (the ledger or the chain head cannot be read) logs
`Error in Herald forward pass` and changes nothing.

**Submitting and re-submitting weights.** Scoring runs once per epoch, but the chain's copy of a
validator's weights ages: once its last update is older than the subnet's activity cutoff (5,000
blocks on netuid 69), the chain stops counting that validator's weights. So once an epoch has been
scored or has failed, the validator submits the latest vector, whichever of the vectors above that
epoch produced, whenever the chain's weight record for its own uid (`LastUpdate`) is at least
`HERALD_WEIGHT_RESUBMIT_BLOCKS` blocks old (default 180). The stored vector changes only when an
epoch is scored or fails, or its scores are replaced (`stale_scores`), and a new vector is submitted
under the same rule.
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

- Only UID 0 and the miner UIDs the latest scored epoch put weight on (`weight_hotkeys`), each
  while it still holds the hotkey recorded for it, may receive weight. A score on any other UID, or
  on a UID another hotkey has since taken (the miner was deregistered and the UID reassigned), moves
  to UID 0: `WEIGHT_VECTOR_BURN reason=hotkey_changed: …` at WARNING. A miner's pay never reaches
  another account.
- Scores with no positive finite entry become all weight on UID 0:
  `WEIGHT_VECTOR_BURN reason=no_scores` at WARNING.
- Before submitting, the validator logs `WEIGHT_VECTOR uids=[…] weights=[…]` at INFO.
- The vector is refused, with `WEIGHT_VECTOR_REFUSED reason=<reason>` at ERROR and no extrinsic
  sent, when:
  - `empty_vector` — no entry is left after conversion;
  - `uid_not_allowed uids=[…] allowed=[…]` — it reaches a UID other than 0 and the allowed miner
    UIDs;
  - `min_allowed_weights_unknown` — the subnet's MinAllowedWeights could not be determined;
  - `below_min_allowed_weights uids=[…] min_allowed_weights=<n>` — it has fewer entries than
    MinAllowedWeights;
  - `read_error (<detail>)` — reading MinAllowedWeights or building the vector failed.

  Nothing is marked as submitted, and the validator tries again at its next weight-setting step
  while the chain's record stays old.

**MinAllowedWeights must be 1.** A burn-only vector (UID 0) has a single entry, and so does a day
on which one miner's pay covers the whole miner emission, so on a subnet whose MinAllowedWeights is
above 1 those vectors are refused and no weights are set. Check `min_allowed_weights` with
`btcli subnet hyperparameters --netuid 69 --network finney`.

### 8.7 Release 0.2.1 (spec version 21)

- `herald/__init__.py` is `0.2.1`, so the spec version, sent as `version_key` with every weight
  submission, is `21`. The spec-20 score checkpoint (`state.npz`) is discarded at startup
  (`Discarding score checkpoint from spec 20; current spec is 21`), so an epoch 0.2.0 already scored
  keeps all its weight on UID 0 (`EPOCH_BURN … reason=stale_scores`) until the next epoch is scored.
- Each contributor's own hotkey is paid (§8, §8.5). `HERALD_INCENTIVE_HOTKEY` and
  `HERALD_BURN_UNEARNED` are retired: they do nothing, are no longer consensus parameters or
  production preflight requirements, and the release no longer carries an incentive hotkey (§8.3).
  While either is set, startup logs a WARNING asking you to remove it.
- Submissions feed rows carry `hotkey`, `coldkey` and `signature`, and rows whose signature does not
  verify are dropped (§8.1). The backend must run the matching release: it serves the signed feed
  and accepts snapshot schema 2.
- The consensus fingerprint changes: `emission_mode` is `miner_hotkeys_v1`, `intake` is
  `backend_submissions_signed_v3`, and `incentive_hotkey` and `burn_unearned` are gone.
- Epoch snapshots are schema 2 (§7): `burn_unearned` and `contributor_share_ppb` are gone, the state
  names the emission mode, and `rewards` and `weights` rows are per miner UID and hotkey.
- `herald_state.json` keeps schema 2 and gains an optional top-level `weight_hotkeys`
  (`{uid: hotkey}`), written only when a miner UID holds weight. 0.2.0 ignores it with a warning
  (`Ignoring unknown top-level Herald state key 'weight_hotkeys'; …`), so it still loads the file.
- Vesting entries that did not start from a signed submission (0.2.0's entries on the incentive
  hotkey, which record no signing coldkey) expire at the first successful scoring pass:
  `LEGACY_VESTING_EXPIRED <n>`.
- `INCENTIVE_WEIGHT`, `INCENTIVE_FULL` and `INCENTIVE_BURN` are replaced by `EPOCH_WEIGHTS`,
  `MINER_WEIGHT` and `EPOCH_BURN` (§8.9).

Upgrading from 0.2.0 is a pull, removing the two retired settings, and a recreate:
```bash
git pull
# Remove HERALD_INCENTIVE_HOTKEY and HERALD_BURN_UNEARNED from .env.
# Only with HERALD_PRODUCTION=true: pin the new fingerprint first (§3).
docker compose --profile validator run --rm --no-deps --entrypoint python \
  validator -m herald.production fingerprint --netuid 69 --subtensor.network finney
#   -> set HERALD_EXPECTED_CONSENSUS_FP in .env (and on the backend) to the printed value
docker compose --profile validator up -d --build validator
# bare-metal: git pull && ./scripts/run_validator.sh
```

### 8.8 Release 0.2.0 (spec version 20)

- Paid every verified article to one shared incentive hotkey (`HERALD_INCENTIVE_HOTKEY`), with
  `HERALD_BURN_UNEARNED` choosing whether the unearned remainder was burned; each snapshot stated
  the contributors' share (`contributor_share_ppb`). Both settings are retired in 0.2.1 (§8.7).
- Built the finney netuid 69 values into the release (§8.3) and expired the vesting entries 0.1
  started from on-chain claims (`LEGACY_VESTING_EXPIRED <n>`).

### 8.9 Log tags

| Line | Level | When |
|---|---|---|
| `OWNER_VALIDATOR_CHECK wallet_hotkey_is_uid0=<True\|False>` | INFO | At startup: whether this validator's wallet hotkey is the hotkey registered at UID 0. `OWNER_VALIDATOR_CHECK unavailable: <error>` at WARNING if it cannot be read. |
| `<name> is set but no longer used: each miner's own hotkey is paid; remove it` | WARNING | At startup, for `HERALD_INCENTIVE_HOTKEY` or `HERALD_BURN_UNEARNED` still set (§8.7). |
| `SUBMISSION_RESULT <submission_id> <reason>` | INFO | Once per candidate tried (§8.1, §8.2), including `hotkey_not_owned`. |
| `SUBMISSION_CREDITED <submission_id> candidate=<i>/<n> hotkey=<ss58>` | INFO | The candidate credited with its article: the `i`-th of the article's `n` candidates in upload order. `hotkey` is the contributor's hotkey the article vests to. |
| `LEGACY_VESTING_EXPIRED <n>` | INFO | A pass expired `n` vesting entries that did not start from a signed submission (§8.7). |
| `VESTING_HELD_UNREGISTERED <n>` | INFO | `n` live articles released nothing because their hotkey is not registered (§8.2). |
| `EPOCH_WEIGHTS epoch=<e> miners=<n> payable_usd=<usd> daily_usd=<usd> burn=<share> alpha_tao=<price> tao_usd=<price> daily_miner_alpha=<alpha>` | INFO | A successful scoring pass: `miners` UIDs have payable USD, totalling `payable_usd`, and `burn` is UID 0's weight (§8.5). |
| `MINER_WEIGHT epoch=<e> uid=<uid> hotkey=<ss58> usd=<usd> w=<w>` | INFO | After `EPOCH_WEIGHTS`, one line per miner UID with payable USD, and its weight. |
| `EPOCH_BURN epoch=<e> reason=<reason>` | INFO for `no_briefs` and `stale_scores`, WARNING otherwise | The day was not scored, or its stored scores were stale, and all its weight went to UID 0 (§8.5). |
| `Submitting Herald epoch <e> weights: the chain record for uid <uid> is <n> blocks old (>= <interval>)` | INFO | The first submission of epoch `e`'s vector passed every submission gate (§8.5). |
| `Re-submitting Herald epoch <e> weights: the chain record for uid <uid> is <n> blocks old (>= <interval>)` | INFO | The same vector is submitted again because the chain's record reached the interval (§8.5). |
| `Weights for uid <uid> are <n> blocks old (< <interval>); skipping resubmission` | INFO | The chain's record is still fresh; nothing is submitted. |
| `Weight commitment pending automatic reveal; skipping resubmission` | INFO | A commit of this hotkey awaits its reveal; nothing is submitted. |
| `No Herald epoch has been scored yet; skipping weight submission` | INFO | No vector exists yet. |
| `Unable to read the age of this uid's weight record: <error>` | WARNING | The record's age could not be read; nothing is submitted. |
| `WEIGHT_VECTOR uids=[…] weights=[…]` | INFO | The u16 vector about to be submitted. |
| `WEIGHT_VECTOR_BURN reason=hotkey_changed: …` | WARNING | Scores on UIDs the latest epoch did not score, or now held by other hotkeys, moved to UID 0 at submission (§8.6). |
| `WEIGHT_VECTOR_BURN reason=no_scores` | WARNING | No positive score; the vector is all weight on UID 0 (§8.6). |
| `WEIGHT_VECTOR_REFUSED reason=<reason>` | ERROR | The vector was refused and no extrinsic was sent (§8.6). |

### 8.10 Feed health from outside

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
  that was not scored publishes nothing either: look for `EPOCH_BURN` and its reason (§8.5).
- **`HERALD_INCENTIVE_HOTKEY is set but no longer used …`** (or `HERALD_BURN_UNEARNED`) — the
  setting is retired and does nothing: remove it from `.env` and recreate the container (§8.7).
  `MAINNET_DEFAULTS_APPLIED: none` on mainnet means `.env` sets every built-in value itself or the
  validator was not started with `--netuid 69` on finney.
- **`Submissions feed: <n> row(s), 0 valid`** — every row failed a §8.1 check. If that persists
  while contributors are submitting, check that the backend runs the matching release: rows without
  `hotkey`, `coldkey` and `signature`, or signed over a different message, are dropped.
  `SUBMISSION_RESULT … hotkey_not_owned` means the row's coldkey does not own its hotkey on chain.
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
  (§8.10).
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
  logs `Submissions feed read failed: …` at WARNING and fails the day
  (`EPOCH_BURN … reason=feed_unavailable`). The feed is read only in epochs with active briefs,
  after the chain time, pricing and the outlet registry succeed.

See also: [miner.md](miner.md) for how contributors submit the articles you verify.
