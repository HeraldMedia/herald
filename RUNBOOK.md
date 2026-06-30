# Herald Operations Runbook

How to deploy and run the Herald subnet (netuid 69): outlet registry, brief board,
validator, and miner. Two paths are supported — **pm2** (scripts in `scripts/`) and
**Docker Compose**. The chain endpoint is a setting; everything stages on testnet first.

---

## 1. Prerequisites

- A Linux host (Ubuntu 22.04+), Python 3.11 or 3.12, `git`.
- `btcli` (Bittensor CLI) for wallets and registration: `pip install bittensor-cli`.
- For pm2: `npm` + `pm2` (installed by `scripts/setup_env.sh`).
- For Docker: Docker + Compose.
- A funded coldkey (TAO) to register and to stake validator alpha.

## 2. Install

```bash
git clone <herald repo> && cd herald
./scripts/setup_env.sh          # creates ../venv_herald and installs deps + package
# or, manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
```

## 3. Wallets

```bash
btcli wallet new_coldkey --wallet.name herald
btcli wallet new_hotkey  --wallet.name herald --wallet.hotkey validator
btcli wallet new_hotkey  --wallet.name herald --wallet.hotkey miner
```

## 4. Register (testnet first, then mainnet SN69)

Stage on testnet to rehearse the full loop before mainnet.

```bash
# Testnet
btcli subnet register --netuid <testnet_uid> --subtensor.network test \
  --wallet.name herald --wallet.hotkey validator

# Mainnet SN69 (clean, empty subnet — no migration)
btcli subnet register --netuid 69 --subtensor.network finney \
  --wallet.name herald --wallet.hotkey validator
```

Repeat for the `miner` hotkey. Validators need subnet-69 **alpha stake** — miners' bonds
are alpha stake on their own hotkey (see §8).

## 5. Configure

```bash
cp .env.example .env       # then edit
```

Set at least `WALLET_NAME`, `HOTKEY_NAME`, `NETUID=69`, `SUBTENSOR_NETWORK`, and
`SERPAPI_API_KEY` (the in-search check). For Docker, also run
`./scripts/wallet-env.sh herald validator >> .env` to inject the hotkey.

## 6. Outlet registry (the trust anchor)

The registry is the system's trust anchor — validators score only against listed outlets.

```bash
# Generate the authority keypair (keep the private key offline / in an HSM)
python -m herald.registry.admin gen-key

# Build and sign the list (start from the seed)
python -m herald.registry.admin add herald/validator/news/outlets.seed.json \
  --outlet-id reuters --tier 1 --domains reuters.com www.reuters.com
python -m herald.registry.admin sign herald/validator/news/outlets.seed.json \
  --key <PRIVATE_HEX> --out outlets.signed.json
```

Point validators at the signed file and the public key:
`HERALD_REGISTRY_PATH=outlets.signed.json` and `HERALD_REGISTRY_PUBKEY=<PUBLIC_HEX>` in `.env`.
(Without a pubkey the seed file loads unsigned — dev only.)

Optionally anchor the version→hash on chain so all validators provably agree on the edition:

```bash
python -m herald.registry.admin anchor outlets.signed.json --effective-block <BLOCK>
# -> prints HRLDREG|<version>|<hash>|<block>; commit it from the authority hotkey:
btcli ...   # or subtensor.commit(wallet, netuid=69, data="HRLDREG|...")
```

Set `HERALD_REGISTRY_AUTHORITY_HOTKEY=<ss58>` on validators; they then reject any loaded list
whose hash/version doesn't match the on-chain anchor.

## 7. Brief board + a funded brief

```bash
./scripts/run_brief_board.sh                       # serves on :8093
# Operator creates and funds a brief (use HERALD_ADMIN_TOKEN header in prod)
curl -s -XPOST localhost:8093/admin/briefs -H 'content-type: application/json' \
  -d '{"title":"Pro-Bittensor coverage","tier":1,"keywords":["bittensor"],
       "start_date":"2026-07-01","end_date":"2026-07-31","reward_pool":5000,"boost":1.0}'
curl -s -XPOST localhost:8093/admin/briefs/<BRIEF_ID>/fund
```

Point validators at it: `HERALD_BRIEFS_ENDPOINT=http://<host>:8093/api/v2/validator/briefs`.
For the public proof page, set `HERALD_RESULTS_ENDPOINT=http://<host>:8093` on the validator.

## 8. Run the validator

```bash
./scripts/run_validator.sh          # pm2: herald_validator
# Docker: docker compose --profile validator up -d --build validator
```

The validator: reads open briefs, reads on-chain commitments, pulls each miner's claims,
runs the oracle, attributes by earliest commit, vests rewards over 30 days with persistence
re-checks, clawbacks + slashes on disappearance, and sets weights. State persists to
`<full_path>/herald_state.json`.

## 9. Run a miner (commit → claim → serve)

```bash
# 1. Commit intent on chain (locks an alpha-stake bond; bond is in atto)
python -m herald.miner.cli commit --brief <BRIEF_ID> --outlet reuters \
  --bond 1000000000000000000 --netuid 69 --wallet-name herald --hotkey miner
#    -> prints the on-chain commitment value

# 2. Do the PR work outside the software; once the article is live:
python -m herald.miner.cli claim --commit <ONCHAIN_VALUE> --url <ARTICLE_URL>

# 3. Run the miner neuron so validators can pull the claim
./scripts/run_miner.sh              # pm2: herald_miner
```

Ensure the miner hotkey holds at least the asserted bond in subnet-69 alpha stake, or claims
are treated as unbacked and pay nothing.

## 10. Monitor

```bash
./scripts/status.sh                 # pm2 process list
pm2 logs herald_validator
curl -s localhost:8093/public/articles      # verified articles (proof, JSON)
curl -s localhost:8093/public/leaderboard   # miner leaderboard (JSON)
curl -s localhost:8093/reporting/export     # exportable report (JSON)
#   Web pages: http://<host>:8093/board (open briefs) and /page (proof + leaderboard)
./scripts/stop.sh                   # stop all Herald processes
```

## 11. Pilot

Run the **standing brief** (always-open, paid from emissions) with the owner miner so the
subnet always produces articles, then measure outside attention from a real coverage
campaign: search ranking of placed URLs, pickup count, and staker interest. The public page
and `/reporting/export` are the evidence surface.

## 12. Troubleshooting

- **Miner paid nothing**: check the hotkey's alpha stake ≥ asserted bond; confirm the article
  URL canonicalizes to a listed outlet; confirm the commit was indexed before the article
  appeared (earliest-commit-wins).
- **`registry signature verification failed`**: the signed file doesn't match
  `HERALD_REGISTRY_PUBKEY`; re-sign with the matching key.
- **No scores set**: the validator found no open/funded briefs — fund one on the brief board.

## 13. Known residuals (operator awareness)

The mechanism, services, and two adversarial-review rounds are complete, but these are
genuinely hard or out of v1 scope and should be planned before a large mainnet rollout:

- **Pre-commit front-running (and value-capping).** Earliest-commit-wins means an attacker who
  blanket-pre-commits to `(outlet, brief)` and later reveals the *same URL* an honest miner
  placed can win attribution; relatedly, an earlier *genuine* low-value (tier-3) placement caps
  a later honest tier-1 placement on the same `(outlet, brief)` to $0 (earliest beats highest
  value). The bond (capital per commit) + one-paid-placement-per-(outlet,brief) cap throttle
  both, but neither is fully closed — they need an attribution-level proof-of-placement, or a
  value-aware tiebreaker.
- **Fetch SSRF is registry-bounded.** The oracle gates fetch on the registry (`outlet_tier`
  runs before any fetch), so validators only fetch approved-outlet domains; `is_safe_fetch_url`
  is defense-in-depth. A residual DNS-rebinding TOCTOU exists (the OS re-resolves on connect)
  but is bounded by that registry gate — keep the registry signed/anchored.
- **Unversioned registry.** A registry published without a `version_id` (defaults to 0) makes
  the version gate reject every claim whose `version_id != 0`. Always publish a `version_id`.
- **Claim-organic on date-less outlets.** The publication-time check only fires when the page
  exposes a parseable `datePublished`/`article:published_time`. Outlets without machine-readable
  dates bypass it. Prefer registry outlets that publish structured dates; consider a per-outlet
  "requires date" flag.
- **Brief authoring.** A brief with no `keywords` makes the rules-only topic check pass every
  article. Always give briefs keywords (and/or enable the LLM tier uniformly).
- **Cross-validator fetch agreement.** Validators fetch live HTML independently; geo/CDN/paywall
  variance can still cause disagreement on the same article in an epoch. Quorum + epoch caching
  reduce but don't eliminate it; a shared content-snapshot consensus is the longer-term fix.
- **Evaluation-epoch boundary skew.** Vesting/slash are gated on `(block - HERALD_EPOCH_LAG) //
  EPOCH_LEN`; the lag shrinks but doesn't remove cross-validator skew at epoch boundaries (EMA
  smooths the residual). A finalized-block-anchored epoch would close it.
- **On-chain brief funding/settlement.** The brief board's `funded` flag is the trusted signal;
  on-chain alpha/TAO settlement (and BTC/stables) is designed-for but not built in v1.
- **LLM judgement determinism.** If `HERALD_USE_LLM_JUDGE=true`, set the SAME `HERALD_REF_MODEL_ID`
  and `LLM_PROVIDER` on every validator (model output drives consensus).
