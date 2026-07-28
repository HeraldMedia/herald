# Herald Miner Guide (Bittensor netuid 69 · finney)

A Herald **miner** is a PR operator or outlet owner who gets a news article published in a
**real mainstream outlet**, then proves it to the subnet. Validators verify each claim with a
code-only oracle and pay miners in emissions. There is **no model to train and no GPU** — your
"work" is real-world media placement plus a small always-on process that serves your claims.

---

## 1. How mining works

```
  read briefs ──▶ COMMIT intent on-chain ──▶ get article published ──▶ CLAIM (attach URL)
                  (salted hash; outlet hidden)                          ──▶ miner serves the REVEAL
                                                                        ──▶ validators verify + pay
```

1. **Read the open briefs.** A brief is a topic/campaign with a reward pool and a date window.
2. **Commit.** Before (or as) you pitch, you commit intent on-chain: a *salted hash* of
   `(brief_id, outlet, hotkey, nonce, version, evidence)`. The **target outlet stays hidden** until
   you reveal, so nobody can front-run your placement. Committing early is how you win attribution.
3. **Get it published.** Land the article in a real outlet that's in the signed **outlet registry**
   (Tiers 1–3, currently 215 outlets).
4. **Claim.** Once the article is live, attach its URL (and a page-text snapshot) to your commit.
5. **Reveal.** Your miner neuron serves the reveal when a validator pulls it (`ClaimSynapse`).
6. **Get paid.** Across all miners, the **earliest valid commit wins** each article, with **one paid
   placement per (outlet, brief)**. Organic/uncommitted coverage pays no one.

---

## 2. What a validator checks (so your claim pays)

Cheapest-first, with early exit — every claim must pass all of these:

| Check | What it means for you |
|---|---|
| **Commitment valid** | Your on-chain commit decodes and matches the reveal. |
| **Outlet in registry** | The domain is an enrolled outlet; its **tier** sets the base multiplier. |
| **URL live** | The article is reachable and not a thin/challenge page. |
| **Real news** | It's editorial, **not** a paid/sponsored/press-release page. |
| **Topic match** | The article is on the brief's topic. |
| **In search index** | The article is discoverable in a real search engine (else the not-indexed floor). |
| **Published after commit** | The publish timestamp is **after** your commit — no claiming pre-existing articles. |

---

## 3. Rewards, vesting & slashing (current defaults)

Payout is multiplicative:

```
payout ≈ BASE_PAYOUT($500) × tier × attribution_evidence × search
```

- **Tier multiplier** — Tier 1 = `1.0`, Tier 2 = `0.6`, Tier 3 = `0.2`.
- **Attribution evidence** (what you hashed into the commit, verified at claim):
  - **Level 2** = pre-committed **text** (draft/quote) found in the article → `1.0`
  - **Level 1** = pre-committed **byline + tight publish window** → `0.7`
  - **Level 0** = bare commit → `0.3` (operators may ratchet L0 toward 0 over time)
- **Search** — indexed = full; not-indexed = `0.5` floor.

**Vesting & persistence:** the reward releases over **~30 daily installments (≈30 days)**. Each
installment requires the article to still be **live**. If it disappears (confirmed dead), remaining
installments are **clawed back** and the hotkey is **slashed** (zeroed across all briefs for a
cooldown). Don't place articles that will be taken down.

**Admission cost:** Bittensor's non-refundable **subnet registration burn** — there is no extra
per-claim bond or escrow.

> These are consensus parameters set by the subnet operator and can change; treat the numbers as
> current defaults, not guarantees.

---

## 4. Hardware

| Resource | Minimum (`min_compute.yml`) | Recommended |
|---|---|---|
| CPU | 2 vCPU | 2–4 vCPU |
| RAM | 4 GB | 4–8 GB |
| Disk | — | 20 GB SSD |
| GPU | none | none |
| Network | — | static public IP, inbound **axon 8091** open, always-on |
| OS | — | Ubuntu 22.04 / 24.04 |

The neuron is lightweight; it mainly needs to be **reachable and always-on** so validators can pull
your reveals.

---

## 5. Setup

### 5.1 Install + wallet + register
```bash
git clone <this repo> herald && cd herald
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install --no-build-isolation -e .

btcli wallet new_coldkey --wallet.name herald_miner
btcli wallet new_hotkey  --wallet.name herald_miner --hotkey m1
btcli subnet register --netuid 69 --wallet.name herald_miner --wallet.hotkey m1 --network finney
```

### 5.2 Configure `.env`
Start from `deploy/miner.env.production.example`:
```ini
HERALD_PRODUCTION=true
NETUID=69
SUBTENSOR_NETWORK=finney
WALLET_NAME=herald_miner
HOTKEY_NAME=m1
AXON_EXTERNAL_IP=<your-public-ip>
AXON_EXTERNAL_PORT=8091
HERALD_CLAIM_STORE=/var/lib/herald/claims.json
# The subnet's canonical backend feed (operator-provided):
HERALD_BRIEFS_ENDPOINT=https://api.heraldmedia.ai/api/v2/validator/briefs
DISABLE_AUTO_UPDATE=true
```

### 5.3 Run the miner neuron (serves your reveals)
```bash
# Docker (default compose service):
docker compose up -d --build miner
docker compose logs -f miner

# or bare-metal:
python neurons/miner.py --netuid 69 --wallet.name herald_miner --wallet.hotkey m1 \
  --axon.external_ip <public-ip> --neuron.disable_auto_update
```
Keep this running — if a validator can't pull your reveal, your claim can't be scored.

---

## 6. The placement workflow (CLI: `herald-miner` / `python -m herald.miner.cli`)

```bash
# 1. See the open briefs
python -m herald.miner.cli briefs

# 2. Commit intent BEFORE/while you pitch (outlet stays hidden on-chain).
#    Add evidence to earn a higher attribution multiplier:
#      --quote "<a sentence you know will appear>"     (or --text-file draft.txt) -> Level 2
#      --author "Jane Doe" --window 2026-07-10:2026-07-20                          -> Level 1
python -m herald.miner.cli commit --brief <brief_id> --outlet <outlet_id> \
  --wallet-name herald_miner --hotkey m1 --quote "the exact line you expect in print"
#   -> prints the on-chain commit value; save it.

# 3. Once the article is published & live, attach the URL (auto-snapshots the page text):
python -m herald.miner.cli claim --commit <onchain_value> --url https://outlet.com/article

# 4. Housekeeping
python -m herald.miner.cli list          # your local claims
python -m herald.miner.cli resubmit --commit <onchain_value>   # re-post a commitment if needed
```

`claims.json` (`HERALD_CLAIM_STORE`) holds your commit→URL mapping and is what the neuron serves —
**back it up**.

---

## 7. Rules & common pitfalls

- **Commit before publication.** The article's publish timestamp must be **after** your commit;
  claiming a pre-existing article is rejected (`published_ts <= commit_ts` fails).
- **Outlet must be in the signed registry.** Off-registry domains earn nothing. Higher tiers pay more.
- **Real editorial only.** Paid/sponsored/advertorial pages fail the real-news check.
- **Stay on topic.** The article must match the brief's topic.
- **Keep it live for the full vesting window (~30 days).** Take-downs trigger clawback + slash.
- **Add real evidence.** A quote or draft (Level 2) pays far more than a bare commit (Level 0).
- **One paid placement per (outlet, brief)** goes to the **earliest valid commit** — commit early.
- **Keep the neuron always-on** with a routable `AXON_EXTERNAL_IP` so validators can pull reveals.

See also: [validator.md](validator.md) for how the other side verifies your work.
