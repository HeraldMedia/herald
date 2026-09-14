# Herald Validator Guide (Bittensor netuid 69 · finney)

A Herald **validator** runs the code-only **verification oracle**: it pulls open briefs from the
subnet backend, fetches each miner's claimed article, scores it, attributes it to the earliest valid
commit, sets weights, and publishes signed epoch snapshots. **No GPU / no ML** — it is network-I/O
bound (web fetches, search-API calls, chain RPC). The optional LLM judge is a *remote* API.

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
  - **Results credentials** — the shared results token, or a per-validator write and read token
    pair (secret, operator-provided).
  - The **signed registry file** (byte-identical to what the backend serves).

Rough cost: server ~$20–40/mo + API keys (ScrapingBee is the main line item), scaling with subnet
claim volume. Stake is locked capital, not spend, and the validator earns emissions.

---

## 3. Consensus fingerprint (read this first)

Weights only agree if every validator uses an **identical** scoring configuration. That config is
hashed into a short **consensus fingerprint**, logged at startup and attached to every published
result. The set that must match fleet-wide includes: the **search provider(s)**, ScrapingBee on/off,
any `api:*` adapters, the LLM-judge setting + pinned model, all scoring tunables (epoch lengths,
payout, attribution, floors), and the trust anchors (pubkeys, authority hotkey).

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
Copy `deploy/validator.env.production.example` to `.env` and fill the blanks. It is the exact
template below — the public trust anchors (endpoint + pubkeys) are baked in; you supply your
wallet/IP and the operator-provided secrets (results token, API keys):
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
# token for the reconciliation feed. Each one set replaces the shared token for its routes.
# HERALD_RESULTS_WRITE_TOKEN=
# HERALD_RESULTS_READ_TOKEN=

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
non-localhost; a results write and read credential (scoped, or the shared token); **ScrapingBee key
present** if the registry has `proxy:` outlets (and an NYT key if it has `api:nyt`); **at least one search provider**; `HERALD_EXPECTED_CONSENSUS_FP`
set and matching the computed fingerprint; and a **live on-chain `HRLDREG` registry anchor** exists.
Any failure prints `production preflight failed: <exact reasons>`.

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
backend confirms an epoch when **`HERALD_QUORUM_REQUIRED` matching enrolled reporters** agree
(default **2** — two independent validators corroborate before results are canonical). A single-
validator bootstrap can lower it to 1, but raise it to ≥2 once independent validators join.

---

## 8. Troubleshooting (real gotchas)

- **`ModuleNotFoundError: No module named 'core'`** — the Dockerfile `pip install -e .` runs before
  `core/`/`neurons/` are copied, so they aren't registered. Set `PYTHONPATH=/app` (already in the
  provided compose).
- **Startup hangs silently** — with a TTY and no `WANDB_API_KEY`, `wandb.init()` blocks on a login
  prompt. Set `WANDB_MODE=disabled` (or provide a key).
- **Runs but never publishes a snapshot** — scoring only fires when `self.step % VALIDATOR_STEPS_INTERVAL == 0`
  (default 240 ≈ 4 h), and **`self.step` persists across restarts** (`state.npz`). Frequent restarts
  march the counter past the scoring step. Lower `HERALD_VALIDATOR_STEPS_INTERVAL` (e.g. 1–10) to
  poll often; actual scoring stays gated to once per epoch and is **not** in the fingerprint.
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
- **`WEIGHT_SUBMISSION_STALLED`** — the validator had an epoch to submit, but the pending
  weight-commit check kept failing, so it is deliberately not setting weights (it will not risk a
  duplicate commit). Set `HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT` to a second finney node and recreate
  the container; it is used only for this check. Keep `SUBTENSOR_NETWORK=finney`, as production
  preflight requires. The check's own log lines show endpoints as `scheme://host[:port]` only. Watch
  liveness from outside with `python scripts/watchdog.py --hotkey <validator ss58>`, run from a host
  checkout with the §4.1 environment (the validator image does not contain `scripts/`): it checks
  the on-chain LastUpdate age and the backend's latest snapshot epoch, and exits 1 on a breach, 2
  when a check cannot run.
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
  logs nothing on the validator, and the feed is read only in epochs with active briefs.

See also: [miner.md](miner.md) for what you're verifying.
