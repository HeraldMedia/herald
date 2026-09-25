# Herald Miner

> **Deprecated.** The miner neuron (`neurons/miner.py`) and the `herald-miner` CLI in this
> directory are not used: validators do not pull `ClaimSynapse` responses or read miner
> commitments, so running them earns nothing. PR firms and PR professionals take part through the
> Herald website with Google sign-in: each registers their own hotkey on netuid 69 from the website
> and signs every submission with their wallet, and validators weight that hotkey by the verified
> value of its articles. See [docs/miner.md](../../docs/miner.md).

## Taking part now

1. Sign in to the Herald website with Google and register your own hotkey on netuid 69, signing
   with a wallet extension (Talisman, SubWallet or Polkadot.js).
2. Pick an open brief. On the brief's page, upload the text you will publish, before publishing
   it, and sign the submission with the coldkey that owns your hotkey.
3. Publish, then add the article's link on the brief's page.
4. Validators check the signature and, on chain, that your coldkey owns the hotkey, then verify the
   article against the outlet's own page: a listed outlet, published inside the brief window and on
   or after the day of the upload, most of the uploaded text present, not paid content, and on
   topic.
5. A verified article vests on your hotkey. Validators set weight on its UID, and the chain emits
   your share to that hotkey.

No miner server or command line is needed to contribute.

Paid posts, advertorials, press-release wires, contributor programs classified as non-editorial,
and outlet-specific branded-content products are not eligible.

## What this directory contains

- `cli.py` — the deprecated `herald-miner` CLI (`briefs`, `commit`, `resubmit`, `claim`, `list`,
  `pull-reveals`).
- `commit.py` — on-chain commitment helpers used by that CLI.
- `claim_store.py` — the local `claims.json` store the miner neuron served.

They remain in the repository, but the validator does not use them and nothing they produce is
rewarded.

## Existing miner setups

- Running the miner neuron or committing with `herald-miner` has no effect on rewards; the neuron
  can be stopped.
- Vesting entries that started from on-chain claims are expired by validators and pay nothing
  further.
- `claims.json` contains commitment nonces. Keep it private if you keep it.
