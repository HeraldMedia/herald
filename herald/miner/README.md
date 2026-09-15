# Herald Miner

> **Deprecated.** Registered miner hotkeys no longer earn on Herald. Validators do not pull
> `ClaimSynapse` responses, read miner commitments or weight miner UIDs; they set weight only on one
> incentive hotkey and burn the rest to UID 0. The miner neuron (`neurons/miner.py`) and the
> `herald-miner` CLI in this directory are deprecated and earn nothing. PR firms and journalists
> take part through the Herald website with Google sign-in; see
> [docs/miner.md](../../docs/miner.md).

## Taking part now

1. Sign in to the Herald website with Google and pick an open brief.
2. On the brief's page, upload the text you will publish, before publishing it.
3. Publish, then add the article's link on the brief's page.
4. Validators verify the article against the outlet's own page: a listed outlet, published inside
   the brief window and on or after the day of the upload, most of the uploaded text present, not
   paid content, and on topic.
5. A verified article earns a share of the alpha the incentive hotkey receives. Earnings
   accumulate on your account and are claimed to a wallet you connect on the website.

No wallet, hotkey or command line is needed to contribute.

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
