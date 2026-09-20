# Herald — Verified Media Placement (Bittensor netuid 69)

Herald is a Bittensor subnet that rewards **verified editorial articles in real news outlets**. PR
firms and PR professionals take part through the Herald website. Validators run an automatic,
code-only **verification oracle** on every submitted article, checking it against the outlet's own
page, and direct the subnet's miner incentive to one **incentive hotkey** in proportion to the
verified value. The part of the day's miner emission that verified value does not cover goes to
UID 0 and is burned.

## How it works

1. **Pick a brief.** A contributor signs in to the Herald website with Google and picks an open
   brief: a topic or campaign, optionally with a date window and a prepaid reward pool.
2. **Upload before publishing.** On the brief's page the contributor uploads the text they will
   publish, before it is published.
3. **Add the link.** Once the article is live, the contributor adds its link on the same page.
4. **Verify.** Once per daily epoch each validator reads the new submissions from the backend and
   checks every article on the outlet's own page: the outlet is in the signed outlet registry, the
   article was published inside the brief window and not before the upload, most of the uploaded
   text appears in it, it is not paid content, and it is on the brief's topic.
5. **Vest.** A verified article is valued by its outlet tier and search presence. The value releases
   in daily installments over a 30-day persistence window while the article stays live. A
   confirmed removal, or a change to paid content, forfeits the remaining installments.
6. **Weight.** Each epoch validators set weight on one incentive hotkey (`HERALD_INCENTIVE_HOTKEY`):
   its share is the epoch's verified USD installments divided by the USD value of the day's miner
   emission, capped at 100%. The rest goes to UID 0 and is burned. If a step that every article
   depends on fails, the whole day is burned.
7. **Earn.** Contributors earn shares of the alpha the incentive hotkey receives, by the value of
   their verified articles. Earnings accumulate on the contributor's account and are claimed to a
   wallet the contributor connects on the website.

No wallet, hotkey or command line is needed to contribute. See [docs/miner.md](docs/miner.md).

**Registered miner hotkeys no longer earn.** Validators do not query miners, read miner
commitments or weight any UID other than 0 and the incentive hotkey's. `herald-miner` and the
miner neuron are deprecated.

## Layout

- `herald/validator/news/` — the submissions feed (`submissions.py`), the oracle (`oracle.py`) and
  its checks (`real_news`, `topic_match`, `textmatch`, `fetch`, `search`), `vesting.py`,
  `pricing.py`, `emission.py` (the incentive and burn vector), `forward.py` (the epoch pass),
  `registry.py`, `publish.py`, `state.py`.
- `herald/registry/admin.py` — operator CLI to manage and sign the outlet registry.
- `neurons/validator.py` — the validator entrypoint.
- `herald/miner/`, `neurons/miner.py` — deprecated; they earn nothing.

## Running

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-build-isolation -e .

# Validator
python neurons/validator.py --netuid 69 --wallet.name <w> --wallet.hotkey <hk> \
  --neuron.disable_auto_update
```

The current release is `0.2.0` (spec version 20). Validators verify the outlet registry's ed25519
signature when `HERALD_REGISTRY_PUBKEY` is set. See `.env.example` for configuration and
[docs/validator.md](docs/validator.md) for the validator guide.

Production deployments use the standalone `herald-backend`; the JSON Brief Board under
`herald/services` is restricted to development and migration. Start validators from
`deploy/validator.env.production.example`. With `HERALD_PRODUCTION=true`, neuron startup fails
closed on a non-mainnet scope, simulator/local endpoints, unsigned feeds or registries, missing
provider or results credentials, a missing or invalid `HERALD_INCENTIVE_HOTKEY`,
consensus-fingerprint drift, or a missing live registry anchor.

## Tests

```bash
python -m pytest -q
```
