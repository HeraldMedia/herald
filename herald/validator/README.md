# Herald Validator

Herald is Bittensor netuid 69 for verified editorial media placement. Validators pull article
claims from miners, verify them against public evidence, maintain vesting and slashing state, and
aggregate the current placement installments by miner. Positive miner totals are normalized to
100% of Bittensor weights; there is no unused-emission or UID-0 remainder.

The core path is rules-based. An LLM is optional and must not be enabled unless every validator
uses the same provider and pinned model.

## What a validator verifies

For every claim, the validator checks:

1. The reveal matches the miner's current on-chain commitment and serving hotkey.
2. The target outlet and registry version match the signed outlet registry.
3. The URL is live and was published after the commitment.
4. The article snapshot matches the validator's direct, proxy, or publisher-API fetch.
5. The URL and article do not match generic or outlet-specific paid-content rules.
6. The article matches the funded brief and, when configured, appears in a search index.
7. Attribution evidence is graded and the strongest, earliest valid claimant wins.

Rewards vest over the configured persistence window. Confirmed removal or conversion to paid
content claws back the remaining vest and slashes the miner for a cooldown.

## Requirements

- Linux, Python 3.11 or 3.12
- A registered validator hotkey with subnet-69 alpha stake
- A publicly routable axon address
- The same consensus-affecting configuration as every other Herald validator
- The offline-signed production outlet registry
- A signed brief-board validator feed
- ScrapingBee credentials for the shipped registry's `proxy:*`-strategy outlets
- SerpAPI and/or Brave credentials for the search-index multiplier

Copy the root configuration template:

```bash
cp .env.example .env
```

At minimum, configure the wallet, network, axon address, brief endpoint, registry trust anchors,
fetch/search providers, and result endpoint. Keep provider availability and every
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

Use a dedicated registered authority hotkey: Bittensor gives each hotkey one commitment slot, so a
miner, validator, or dispute hotkey would overwrite (or be overwritten by) the registry anchor.
Publish only after inspecting the printed `HRLDREG|...` value:

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

When an authority hotkey is configured, a missing or mismatched anchor fails closed.

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

An explicitly empty, valid feed clears the score vector and submits no weights. A network failure
uses the existing brief cache when available; it is not treated as an authoritative empty feed.

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
`validator_state` volume. It also applies a configurable memory limit because Bittensor reads a
raw dendrite response before the bounded `ClaimSynapse` model parses it.

The score checkpoint records the producing spec version; a mismatch discards old scores instead
of publishing an old emission model under a new version key. The Herald ledger separately records
the last scored and last successfully submitted weight epochs, preventing one daily allocation
from being resubmitted at each shorter Bittensor weight-update interval. Back up and restore both
state files together.

Set `AXON_EXTERNAL_IP` when automatic public-address discovery is not reliable. If the public port
differs from the listen port, also set `AXON_EXTERNAL_PORT`.

## Several validators

- Compare the 16-character consensus fingerprint in every validator's logs and published results.
- Set `HERALD_REGISTRY_ENDPOINT` to the backend base URL. Each validator fetches the activated
  edition but independently verifies its Ed25519 signature and finalized authority anchor before
  caching it; a new anchor without its matching edition fails closed.
- With `HERALD_RESULTS_ENDPOINT` set, each evaluation publishes an immutable hotkey-signed epoch
  snapshot containing exact micro-USD pool accounting, daily contributions, lifecycle state, and
  the intended normalized vector. A second signed receipt follows weight submission.
- Roll out consensus changes to the entire fleet together; automatic git updates should stay off.
- Give every validator the same registry edition, anchor, brief key, provider set, quorum, and LLM
  configuration.
- Bootstrap a new validator's vesting state from published results before its first scoring epoch:

```bash
python -m herald.validator.news.bootstrap \
  --results-url "$HERALD_RESULTS_ENDPOINT" \
  --state-path <full_path>/herald_state.json \
  --netuid 69 --network finney
```

Claim reconciliation uses the result board only as a hint; every imported reveal is fully
reverified against the chain, registry, and article.

## Verification and monitoring

```bash
source .venv/bin/activate
python -m pytest -q
./scripts/status.sh
pm2 logs herald_validator
curl -fsS https://herald-api.example/public/articles
```

Before mainnet, rehearse with at least two validators and several miners. Confirm identical
fingerprints, restart recovery, result reconciliation, persistence checks, normalized weights, and a real
`set_weights` extrinsic.
