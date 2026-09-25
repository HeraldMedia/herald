# Herald — Verified Media Placement (Bittensor netuid 69)

Herald is a Bittensor subnet that rewards **verified editorial articles in real news outlets**. PR
firms and PR professionals take part through the Herald website, each with **their own hotkey** on
the subnet. Validators run an automatic, code-only **verification oracle** on every submitted
article, checking it against the outlet's own page, and weight each contributor's hotkey by the
verified value of their articles. The share of the miner emission that verified value does not
cover goes to UID 0 and is burned.

## How it works

1. **Register a hotkey.** A contributor signs in to the Herald website with Google and registers
   their own hotkey on netuid 69 from the website, signing with a wallet extension (Talisman,
   SubWallet or Polkadot.js). No miner server is needed.
2. **Pick a brief.** The contributor picks an open brief: a topic or campaign, optionally with a
   date window and a prepaid reward pool.
3. **Upload before publishing.** On the brief's page the contributor uploads the text they will
   publish, before it is published, and signs the submission with the coldkey that owns their
   hotkey.
4. **Add the link.** Once the article is live, the contributor adds its link on the same page.
5. **Verify.** Once per daily epoch each validator reads the new submissions from the backend,
   checks each signature and, on chain, that the signing coldkey owns the hotkey, and checks every
   article on the outlet's own page: the outlet is in the signed outlet registry, the article was
   published inside the brief window and not before the upload, most of the uploaded text appears
   in it, it is not paid content, and it is on the brief's topic.
6. **Vest.** A verified article is valued by its outlet tier and search presence. The value releases
   to the contributor's hotkey in daily installments over a 30-day persistence window while the
   article stays live. A confirmed removal, or a change to paid content, forfeits the remaining
   installments. While the hotkey is not registered, installments hold and catch up once it
   registers again.
7. **Weight.** Each epoch validators weight every contributor's UID by its payable USD divided by
   the USD value of the day's miner emission, and give UID 0 the rest, which is burned. When the
   contributors' total is larger than the day's emission they share all of it pro rata. A day on
   which a step every article depends on fails is burned whole.
8. **Earn.** The chain emits each UID's share to the contributor's own hotkey, as for any Bittensor
   miner.

No miner server or command line is needed to contribute. See [docs/miner.md](docs/miner.md).

**The miner neuron is not used.** Validators do not query miners or read miner commitments; a
hotkey earns only through signed submissions credited to it. `herald-miner` and the miner neuron
are deprecated.

## Layout

- `herald/validator/news/` — the submissions feed (`submissions.py`), the oracle (`oracle.py`) and
  its checks (`real_news`, `topic_match`, `textmatch`, `fetch`, `search`), `vesting.py`,
  `signatures.py` (the submission signature), `pricing.py`, `emission.py` (the weight vector over
  the miners' UIDs), `forward.py` (the epoch pass), `registry.py`, `publish.py`, `state.py`.
- `herald/registry/admin.py` — operator CLI to manage and sign the outlet registry.
- `neurons/validator.py` — the validator entrypoint.
- `herald/miner/`, `neurons/miner.py` — deprecated; contributors do not need them.

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

The current release is `0.2.1` (spec version 21). Validators verify the outlet registry's ed25519
signature when `HERALD_REGISTRY_PUBKEY` is set. See `.env.example` for configuration and
[docs/validator.md](docs/validator.md) for the validator guide.

Production deployments use the standalone `herald-backend`; the JSON Brief Board under
`herald/services` is restricted to development and migration. Start validators from
`deploy/validator.env.production.example`; on finney netuid 69 the release supplies the public
settings (endpoints, pubkeys, authority hotkey, epoch alignment), so an operator adds only a wallet,
API keys and the results credential. With `HERALD_PRODUCTION=true`, neuron startup fails closed on a
non-mainnet scope, simulator/local endpoints, unsigned feeds or registries, missing provider or
results credentials, consensus-fingerprint drift, or a missing live registry anchor.
`HERALD_INCENTIVE_HOTKEY` and `HERALD_BURN_UNEARNED` are retired in `0.2.1`; startup warns while
either is set.

## Tests

```bash
python -m pytest -q
```
