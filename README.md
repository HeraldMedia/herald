# Herald — Verified Media Placement (Bittensor netuid 69)

Herald is a Bittensor subnet for **verified mainstream-media placement**. Miners are PR
operators and outlet owners who get a news article published in a real outlet, then submit
the URL. Validators run an automatic, code-only **public-web verification oracle** on each
claimed article and pay miners in emissions. It reuses the proven Bittensor neuron / brief /
weight-setting shape; the new engineering is the verification oracle and commit-reveal
attribution.

## How it works

1. **Commit.** A miner reads the open briefs and commits intent on chain — a salted hash of
   `(brief_id, outlet, hotkey, nonce, bond, version)` — locking an alpha-stake bond. The
   target outlet stays hidden until reveal.
2. **Claim.** Once the article is live, the miner attaches the URL and serves the reveal when
   a validator pulls it (`ClaimSynapse`).
3. **Verify.** For each claim the validator runs the oracle, cheapest-first with early-exit:
   commitment valid → outlet tier (registry) → URL live → real-news (not paid) → topic match →
   in search index. Every score is rebuildable from saved evidence.
4. **Attribute.** Across all claims, the earliest valid commit wins each article, with one paid
   placement per (outlet, brief). Organic/uncommitted articles pay no one.
5. **Vest & slash.** The reward releases in installments over a 30-day persistence window. If
   the article disappears, remaining installments are clawed back and the hotkey is slashed
   (zeroed across all briefs for a cooldown).

## Layout

- `herald/validator/news/` — the oracle (`oracle.py`), checks (`real_news`, `topic_match`,
  `fetch`, `search`), `attribution.py`, `commit_index.py`, `bonds.py`, `vesting.py`,
  `slashing.py`, `reward.py`, `forward.py`, `registry.py`, `state.py`.
- `herald/miner/` — `commit.py`, `claim_store.py`, `cli.py`.
- `herald/registry/admin.py` — operator CLI to manage and sign the outlet registry.
- `neurons/{miner,validator}.py` — entrypoints.

## Running

```bash
pip install -e .

# Validator
python neurons/validator.py --netuid 69 --wallet.name <w> --wallet.hotkey <hk>

# Miner: commit to an outlet, then attach the URL once published
python -m herald.miner.cli commit --brief <id> --outlet <outlet_id> --bond <atto>
python -m herald.miner.cli claim --commit <onchain_value> --url <article_url>
python neurons/miner.py --netuid 69 --wallet.name <w> --wallet.hotkey <hk>
```

Validators verify the outlet registry's ed25519 signature when `HERALD_REGISTRY_PUBKEY` is set.
See `.env.example` for configuration.

## Tests

```bash
pytest tests/news/
```
