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
  - Registry **authority hotkey** (SS58) and the **results token** (secret — operator-provided).
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
Start from `deploy/validator.env.production.example`:
```ini
HERALD_PRODUCTION=true
HERALD_PRODUCTION_NETUID=69
NETUID=69
SUBTENSOR_NETWORK=finney
WALLET_NAME=herald_vali
HOTKEY_NAME=v1
AXON_EXTERNAL_IP=<your-public-ip>
VALIDATOR_AXON_PORT=8092

HERALD_BRIEFS_ENDPOINT=https://api.heraldmedia.ai/api/v2/validator/briefs
HERALD_BRIEFS_PUBKEY=a1b3e1d6e412a1a97d694ce5af196411e1bc2b4cc250d83ab92d0111b7b1af9a
HERALD_REQUIRE_SIGNED_BRIEFS=true
HERALD_RESULTS_ENDPOINT=https://api.heraldmedia.ai
HERALD_RESULTS_TOKEN=<operator-provided>

HERALD_REGISTRY_PATH=/secure/herald/outlets.signed.json
HERALD_REGISTRY_PUBKEY=9bc2326f0019bcbfe279948222e1fbc6d0b281bb50bc7569c3551ede764aede6
HERALD_REGISTRY_AUTHORITY_HOTKEY=<operator-provided SS58>
HERALD_REQUIRE_SIGNED_REGISTRY=true

SCRAPINGBEE_API_KEY=<key>
BRAVE_API_KEY=<key>            # or SERPAPI_API_KEY — match the fleet
HERALD_QUORUM_THRESHOLD=1
HERALD_ALLOW_LOCAL_FETCH=false
HERALD_USE_LLM_JUDGE=false
DISABLE_AUTO_UPDATE=true

HERALD_EXPECTED_CONSENSUS_FP=<from `python -m herald.production fingerprint`>
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
non-localhost; results token set; **ScrapingBee key present** if the registry has `proxy:` outlets
(and an NYT key if it has `api:nyt`); **at least one search provider**; `HERALD_EXPECTED_CONSENSUS_FP`
set and matching the computed fingerprint; and a **live on-chain `HRLDREG` registry anchor** exists.
Any failure prints `production preflight failed: <exact reasons>`.

---

## 6. Epoch confirmation & quorum

Validators publish **hotkey-signed epoch snapshots** to `POST /api/v3/validator/snapshots`. The
backend confirms an epoch when **`HERALD_QUORUM_REQUIRED` matching enrolled reporters** agree
(default **2** — two independent validators corroborate before results are canonical). A single-
validator bootstrap can lower it to 1, but raise it to ≥2 once independent validators join.

---

## 7. Troubleshooting (real gotchas)

- **`ModuleNotFoundError: No module named 'core'`** — the Dockerfile `pip install -e .` runs before
  `core/`/`neurons/` are copied, so they aren't registered. Set `PYTHONPATH=/app` (already in the
  provided compose).
- **Startup hangs silently** — with a TTY and no `WANDB_API_KEY`, `wandb.init()` blocks on a login
  prompt. Set `WANDB_MODE=disabled` (or provide a key).
- **Runs but never publishes a snapshot** — scoring only fires when `self.step % VALIDATOR_STEPS_INTERVAL == 0`
  (default 240 ≈ 4 h), and **`self.step` persists across restarts** (`state.npz`). Frequent restarts
  march the counter past the scoring step. Lower `HERALD_VALIDATOR_STEPS_INTERVAL` (e.g. 1–10) to
  poll often; actual scoring stays gated to once per epoch and is **not** in the fingerprint.
- **Can't see what it's doing** — INFO logs are suppressed by default (WARNING level). Add
  `--logging.debug` to the run command to see `step(N)` / `Herald forward pass` / snapshot lines.
- **Occasional `UnknownBlock: Expect block number from id`** — transient inconsistency from the
  shared public finney endpoint (a load-balanced pool). The neuron retries and recovers; if it's
  *frequent*, point `SUBTENSOR_CHAIN_ENDPOINT` at a dedicated/local subtensor node. (Consistent
  failures are almost never the endpoint — check the step cadence above first.)
- **`403` publishing snapshots** — your hotkey isn't enrolled/chain-verified as a reporter yet; the
  operator must `POST /admin/reporters` and the finalized-chain indexer must confirm your permit.

See also: [miner.md](miner.md) for what you're verifying.
