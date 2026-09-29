# Herald Validator

Herald is Bittensor netuid 69 for verified editorial media placement. PR firms and PR professionals
take part through the Herald website: each registers their own hotkey on the subnet from the website
and signs every submission with the coldkey that owns it. Validators read those submissions from the
backend, check each signature and, on chain, the hotkey's owner, verify each article against the
outlet's own page, vest its value on the contributor's hotkey, and set weights on the contributors'
UIDs and UID 0 only.

Each miner UID's weight is its payable USD for the epoch divided by the USD value of the day's miner
emission, and UID 0 receives the rest, which is burned. When the miners' total is larger than the
day's emission they share all of it pro rata and nothing is burned. A day that is not scored (no
briefs, or a failed shared step) puts all its weight on UID 0, and the next scored epoch catches up
the installments it skipped.

The core path is rules-based. An LLM is optional and must not be enabled unless every validator
uses the same provider and pinned model.

## What a validator verifies

For every new submission, the validator checks, in order:

1. The submission is signed by its coldkey for its hotkey (checked when the feed is read), and that
   coldkey owns the hotkey on chain at the scoring block (`hotkey_not_owned` otherwise).
2. The submission's brief is in the signed active brief feed.
3. The outlet is in the signed outlet registry and is fetched directly or through the proxy.
4. The URL is live and states a publication time.
5. Publication is no later than chain time, at most `HERALD_MAX_ARTICLE_AGE_DAYS` before it, and,
   for a brief with an end date, inside the brief window (start date minus
   `HERALD_PUBLISH_BUFFER_DAYS` through end date).
6. The article was not published before the contributor uploaded their text. When the page states an
   exact publication time (a time of day with `Z` or an explicit UTC offset), the upload must be at
   or before it. A date alone, a time with no offset, or exactly midnight in the stated offset (how
   many sites render a date alone) only has to fall on or after the upload's UTC day, and is judged
   by the date the outlet states, in its own offset; the brief window uses that date too.
7. At least `HERALD_DRAFT_MATCH_THRESHOLD` of the uploaded text appears in the article the
   validator fetched.
8. The URL and article do not match generic or outlet-specific paid-content rules.
9. The article matches the brief's topic. Search-index presence then sets the value multiplier.

Several contributors may submit the same article. Its submissions are tried in order of upload
time, then submission id (at most `HERALD_MAX_CANDIDATES_PER_ARTICLE` of the earliest), and the first
that passes every check is credited: the earliest matching draft wins. An article already credited
is not verified again. New articles are verified first come, first served, in order of their
earliest upload, at most `HERALD_MAX_SUBMISSIONS_PER_EPOCH` per epoch.

Rewards vest on the contributor's hotkey over the configured persistence window while the article
stays live. While the hotkey is not registered, releases hold, and the missed installments are
released once it registers again, up to the article's maximum age. Confirmed removal or conversion
to paid content claws back the remaining vest. There is no slashing.

When the verified brief feed is empty, or a step every article depends on fails (chain time,
pricing, the outlet registry, the submissions feed or the hotkey owner reads), the day is not
scored: all its weight goes to UID 0 (`EPOCH_BURN`). See
[docs/validator.md](../../docs/validator.md) §8 for the submissions feed, settings, pricing inputs,
weight vector checks and log tags.

## Requirements

- Linux, Python 3.11 or 3.12
- A registered validator hotkey with subnet-69 alpha stake
- Outbound HTTPS and chain RPC only: a validator serves no axon and takes no inbound traffic
- The same consensus-affecting configuration as every other Herald validator (on finney netuid 69
  the release supplies the backend endpoints, trust anchors and epoch alignment:
  `herald/network_profile.py`)
- The offline-signed production outlet registry
- A signed brief-board validator feed
- `HERALD_RESULTS_ENDPOINT` with a results read credential (the submissions feed) and write
  credential (reports)
- ScrapingBee credentials for the shipped registry's `proxy:*`-strategy outlets
- SerpAPI and/or Brave credentials for the search-index multiplier
- Outbound HTTPS to CoinGecko for the TAO/USD price
- A subnet MinAllowedWeights of 1, so single-entry vectors (all weight on UID 0, or on one miner
  UID) are accepted

Copy the root configuration template:

```bash
cp .env.example .env
```

At minimum, configure the wallet, network, brief endpoint, registry trust anchors,
fetch/search providers, and results endpoint and credentials. Keep provider availability and every
`HERALD_*` consensus value identical across the fleet.

## Registry trust

The checked-in `herald/validator/news/outlets.json` contains the researched 215-outlet registry but
is intentionally unsigned. Do not use it unsigned in production.

```bash
umask 077
python -m herald.registry.admin gen-key --out-key herald-registry.ed25519.key
REGISTRY_PUBKEY=$(python -m herald.registry.admin public-key \
  --key-file herald-registry.ed25519.key)
python -m herald.registry.admin prepare herald/validator/news/outlets.json \
  --version 3 --out outlets.v3.json
python -m herald.registry.admin sign outlets.v3.json \
  --key-file herald-registry.ed25519.key --out outlets.signed.json
python -m herald.registry.admin verify outlets.signed.json --pubkey "$REGISTRY_PUBKEY"
python -m herald.registry.admin anchor outlets.signed.json --effective-block <BLOCK>
```

Registry editions advance one version at a time. Validators keep the previous signed edition while
a finalized anchor's effective block is still in the future, then fail closed until the backend
serves the newly active edition for their network and netuid.

Use a dedicated registered authority hotkey: Bittensor gives each hotkey one commitment slot, so
any other commitment from the same hotkey would overwrite (or be overwritten by) the registry
anchor. Publish only after inspecting the printed `HRLDREG|...` value:

```bash
python -m herald.registry.admin publish-anchor outlets.signed.json \
  --pubkey "$REGISTRY_PUBKEY" --effective-block <BLOCK> \
  --wallet-name <wallet> --wallet-hotkey <dedicated-authority> \
  --netuid 69 --network finney --yes
python -m herald.registry.admin verify-live-anchor outlets.signed.json \
  --pubkey "$REGISTRY_PUBKEY" --authority <AUTHORITY_SS58> \
  --netuid 69 --network finney
```

Copy the finalized on-chain anchor and run `preflight` before rolling the fleet:

```bash
python -m herald.registry.admin preflight outlets.signed.json \
  --pubkey "$REGISTRY_PUBKEY" --anchor 'HRLDREG|...'
```

Then configure:

```dotenv
HERALD_REGISTRY_PATH=/secure/config/outlets.signed.json
HERALD_REGISTRY_PUBKEY=<PUBLIC_HEX>
HERALD_REQUIRE_SIGNED_REGISTRY=true
HERALD_REGISTRY_AUTHORITY_HOTKEY=<AUTHORITY_SS58>
```

When an authority hotkey is configured, a missing or mismatched anchor fails closed and the day is
not scored.

For a guarded two-validator PM2 handoff after a testnet canary has scored its target epoch:

```bash
CANARY_TARGET_EPOCH=<completed-epoch> ./scripts/rollout_validator_quorum.sh
./scripts/verify_validator_quorum.sh
```

The rollout refuses unsigned registry/brief configuration, independently verifies the live owner
anchor, and will not replace the canary before its persisted `last_scored_epoch` reaches the target.
It gives v1 and v2 separate state directories and leaves local simulator fetching disabled.

## Brief-feed trust

The canonical standalone backend uses a separate online signing key. Generate it independently from the offline
registry key, derive its public half, and inject the private value through the deployment secret
manager as `HERALD_BRIEFS_PRIVKEY`:

```bash
python -m herald.registry.admin gen-key --out-key herald-briefs.ed25519.key
python -m herald.registry.admin public-key --key-file herald-briefs.ed25519.key
```

Configure that key in `herald-backend/.env`; keep the resulting env file outside the repository
with mode `0600`, and generate independent high-entropy values for every write token.
Changing the brief public key is a consensus rollout: update every validator together because the
key is part of the consensus fingerprint.

Validators use only the public half:

```dotenv
HERALD_BRIEFS_ENDPOINT=https://herald-api.example/api/v2/validator/briefs
HERALD_BRIEFS_PUBKEY=<PUBLIC_HEX>
HERALD_REQUIRE_SIGNED_BRIEFS=true
HERALD_BRIEFS_MAX_AGE=900
```

An explicitly empty, valid feed puts all of the epoch's weight on UID 0
(`EPOCH_BURN epoch=<e> reason=no_briefs`). A network failure uses the existing brief cache when
available; it is not treated as an authoritative empty feed.

## Miner hotkeys and submissions

```dotenv
# Built in on finney netuid 69; set only on another network, or to deviate on purpose:
# HERALD_RESULTS_ENDPOINT=https://herald-api.example
HERALD_RESULTS_READ_TOKEN=<READ_TOKEN>
```

- Contributors register their own hotkeys; a validator configures none. Each feed row carries the
  contributor's `hotkey`, the `coldkey` and that coldkey's `signature` over the submission
  (`herald/validator/news/signatures.py`). A row whose signature does not verify is dropped, and a
  submission whose coldkey does not own its hotkey on chain at the scoring block is rejected
  (`hotkey_not_owned`). A credited article vests on that hotkey and is paid on whichever UID it
  holds.
- `HERALD_INCENTIVE_HOTKEY` and `HERALD_BURN_UNEARNED` are retired in `0.2.1`: they do nothing and
  are not in the consensus fingerprint, and startup logs a WARNING while either is set. Remove them
  from `.env`.
- The validator reads `GET /api/v4/validator/submissions` with `HERALD_RESULTS_READ_TOKEN`, or the
  shared `HERALD_RESULTS_TOKEN`. A feed that cannot be read fails the day.
- Uploaded draft text is used only for verification. The validator never publishes, stores or logs
  it.
- `HERALD_DRAFT_MATCH_THRESHOLD` (default `0.6`), `HERALD_PUBLISH_BUFFER_DAYS` (`3`),
  `HERALD_MAX_ARTICLE_AGE_DAYS` (`21`), `HERALD_MAX_SUBMISSIONS_PER_EPOCH` (`500`) and
  `HERALD_MAX_CANDIDATES_PER_ARTICLE` (`10`) are consensus values.

## Run

Install and start with PM2:

```bash
./scripts/setup_env.sh
HERALD_VALIDATOR_ENV_FILE=/secure/config/validator.env ./scripts/run_validator.sh
pm2 logs herald_validator
```

For production, start from `deploy/validator.env.production.example`. Compute the expected
fingerprint with `python -m herald.production fingerprint`, set it identically across the backend
and every validator, then run `python -m herald.production check-validator` before rollout.

Or use Compose:

```bash
VALIDATOR_ENV_FILE=/secure/config/validator.env \
  docker compose --profile validator up -d --build validator
docker compose logs -f validator
```

Compose persists the wallet, score checkpoint, Herald ledger, and logs in the
`validator_state` volume. It also applies a configurable memory limit.

The score checkpoint records the producing spec version (21 for release `0.2.1`); a mismatch
discards old scores instead of publishing an old emission model under a new version key. The
Herald ledger separately records the last scored epoch, so an epoch is scored once, and the last
successfully submitted weight epoch, as bookkeeping. Back up and restore both state files together.

Scoring runs once per epoch, but the latest vector is submitted again whenever the chain's weight
record for this validator's uid is at least `HERALD_WEIGHT_RESUBMIT_BLOCKS` blocks old (default
180), so the chain's copy stays inside the subnet's activity cutoff. Every submission keeps the
pending-commit skip and the vector checks. With commit-reveal the record is refreshed about once
per tempo. See `docs/validator.md` §8.5.

A validator publishes no address on chain and listens on no port: it reads briefs and
contributor submissions from the Herald backend, reads the chain over RPC and fetches article
and price pages, all outbound. `AXON_EXTERNAL_IP` and `AXON_EXTERNAL_PORT` are miner settings.

## Several validators

- Compare the 16-character consensus fingerprint in every validator's logs and published results.
- Set `HERALD_REGISTRY_ENDPOINT` to the backend base URL. Each validator fetches the activated
  edition but independently verifies its Ed25519 signature and finalized authority anchor before
  caching it; a new anchor without its matching edition fails closed.
- With `HERALD_RESULTS_ENDPOINT` set, each scored epoch publishes an immutable hotkey-signed epoch
  snapshot (schema 2) containing exact micro-USD pool accounting, each miner UID's payable USD
  with its hotkey, lifecycle state, and the intended vector on UID 0 and the miner UIDs. A second
  signed receipt follows weight submission. An epoch whose scoring failed publishes no snapshot.
- The backend confirms an epoch when `HERALD_QUORUM_REQUIRED` enrolled reporters agree. Set it to 1
  when a single operator's validator is the only confirming reporter.
- Roll out consensus changes to the entire fleet together; automatic git updates should stay off.
- Give every validator the same registry edition, anchor, brief key, provider set, quorum, and LLM
  configuration.

## Verification and monitoring

```bash
source .venv/bin/activate
python -m pytest -q
./scripts/status.sh
pm2 logs herald_validator
curl -fsS https://herald-api.example/public/articles
```

Watch for `SUBMISSION_RESULT`, `SUBMISSION_CREDITED`, `EPOCH_WEIGHTS`, `MINER_WEIGHT`,
`EPOCH_BURN`, `VESTING_HELD_UNREGISTERED` and `WEIGHT_VECTOR_REFUSED` lines;
[docs/validator.md](../../docs/validator.md) §8.9 lists every tag.

Before mainnet, rehearse with at least two validators and several submissions from different
contributors' hotkeys. Confirm identical fingerprints, restart recovery, persistence checks,
`EPOCH_WEIGHTS`, `MINER_WEIGHT` and `EPOCH_BURN` lines, and a real `set_weights` extrinsic that
carries only UID 0 and the credited contributors' UIDs.
