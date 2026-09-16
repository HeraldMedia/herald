# Herald Validator

Herald is Bittensor netuid 69 for verified editorial media placement. PR firms and journalists
submit articles through the Herald website. Validators read those submissions from the backend,
verify each article against the outlet's own page, vest its value on one incentive hotkey
(`HERALD_INCENTIVE_HOTKEY`), and set weights on that hotkey and UID 0 only. The incentive hotkey
receives the epoch's verified USD installments divided by the USD value of the day's miner
emission, at most 100%; UID 0 receives the rest, which is burned. Registered miner hotkeys receive
no weight.

The core path is rules-based. An LLM is optional and must not be enabled unless every validator
uses the same provider and pinned model.

## What a validator verifies

For every new submission, the validator checks, in order:

1. The submission's brief is in the signed active brief feed.
2. The outlet is in the signed outlet registry and is fetched directly or through the proxy.
3. The URL is live and states a publication time.
4. Publication is no later than chain time, at most `HERALD_MAX_ARTICLE_AGE_DAYS` before it, and,
   for a brief with an end date, inside the brief window (start date minus
   `HERALD_PUBLISH_BUFFER_DAYS` through end date).
5. The article was published on or after the UTC day the contributor uploaded their text.
6. At least `HERALD_DRAFT_MATCH_THRESHOLD` of the uploaded text appears in the article the
   validator fetched.
7. The URL and article do not match generic or outlet-specific paid-content rules.
8. The article matches the brief's topic. Search-index presence then sets the value multiplier.

Rewards vest over the configured persistence window while the article stays live. Confirmed
removal or conversion to paid content claws back the remaining vest. There is no slashing.

When the verified brief feed is empty, or a step every article depends on fails (the incentive
hotkey check, chain time, pricing, the outlet registry or the submissions feed), the validator
puts all weight on UID 0 for the day. See [docs/validator.md](../../docs/validator.md) §8 for the
submissions feed, settings, pricing inputs, weight vector checks and log tags.

## Requirements

- Linux, Python 3.11 or 3.12
- A registered validator hotkey with subnet-69 alpha stake
- A publicly routable axon address
- The same consensus-affecting configuration as every other Herald validator, including
  `HERALD_INCENTIVE_HOTKEY`
- The offline-signed production outlet registry
- A signed brief-board validator feed
- `HERALD_RESULTS_ENDPOINT` with a results read credential (the submissions feed) and write
  credential (reports)
- ScrapingBee credentials for the shipped registry's `proxy:*`-strategy outlets
- SerpAPI and/or Brave credentials for the search-index multiplier
- Outbound HTTPS to CoinGecko for the TAO/USD price
- A subnet MinAllowedWeights of 1, so single-entry burn vectors are accepted

Copy the root configuration template:

```bash
cp .env.example .env
```

At minimum, configure the wallet, network, axon address, brief endpoint, registry trust anchors,
fetch/search providers, results endpoint and credentials, and the incentive hotkey. Keep provider
availability and every `HERALD_*` consensus value identical across the fleet.

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
anchor. The authority hotkey must also differ from `HERALD_INCENTIVE_HOTKEY`. Publish only after
inspecting the printed `HRLDREG|...` value:

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
burned.

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

An explicitly empty, valid feed puts all weight on UID 0 for the epoch
(`INCENTIVE_BURN epoch=<e> reason=no_briefs`). A network failure uses the existing brief cache when
available; it is not treated as an authoritative empty feed.

## Incentive hotkey and submissions

```dotenv
HERALD_INCENTIVE_HOTKEY=<INCENTIVE_SS58>
HERALD_RESULTS_ENDPOINT=https://herald-api.example
HERALD_RESULTS_READ_TOKEN=<READ_TOKEN>
```

- `HERALD_INCENTIVE_HOTKEY` is provided by the subnet operator and must be identical on every
  validator; it is part of the consensus fingerprint. Production preflight requires a valid SS58
  address that differs from `HERALD_REGISTRY_AUTHORITY_HOTKEY`. At scoring time the day is burned
  if the hotkey is not registered, is this validator's own hotkey, or holds UID 0.
- The validator reads `GET /api/v4/validator/submissions` with `HERALD_RESULTS_READ_TOKEN`, or the
  shared `HERALD_RESULTS_TOKEN`. A feed that cannot be read burns the day.
- Uploaded draft text is used only for verification. The validator never publishes, stores or logs
  it.
- `HERALD_DRAFT_MATCH_THRESHOLD` (default `0.6`), `HERALD_PUBLISH_BUFFER_DAYS` (`3`),
  `HERALD_MAX_ARTICLE_AGE_DAYS` (`21`) and `HERALD_MAX_SUBMISSIONS_PER_EPOCH` (`500`) are consensus
  values.

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

The score checkpoint records the producing spec version (20 for release `0.2.0`); a mismatch
discards old scores instead of publishing an old emission model under a new version key. The
Herald ledger separately records the last scored epoch, so an epoch is scored once, and the last
successfully submitted weight epoch, as bookkeeping. Back up and restore both state files together.

Scoring runs once per epoch, but the latest vector is submitted again whenever the chain's weight
record for this validator's uid is at least `HERALD_WEIGHT_RESUBMIT_BLOCKS` blocks old (default
180), so the chain's copy stays inside the subnet's activity cutoff. Every submission keeps the
pending-commit skip and the vector checks. With commit-reveal the record is refreshed about once
per tempo. See `docs/validator.md` §8.5.

Set `AXON_EXTERNAL_IP` when automatic public-address discovery is not reliable. If the public port
differs from the listen port, also set `AXON_EXTERNAL_PORT`.

## Several validators

- Compare the 16-character consensus fingerprint in every validator's logs and published results.
- Set `HERALD_REGISTRY_ENDPOINT` to the backend base URL. Each validator fetches the activated
  edition but independently verifies its Ed25519 signature and finalized authority anchor before
  caching it; a new anchor without its matching edition fails closed.
- With `HERALD_RESULTS_ENDPOINT` set, each scored epoch publishes an immutable hotkey-signed epoch
  snapshot containing exact micro-USD pool accounting, daily contributions, lifecycle state, and
  the intended vector on UID 0 and the incentive hotkey's UID. A second signed receipt follows
  weight submission. An epoch burned by a failed step publishes no snapshot.
- The backend confirms an epoch when `HERALD_QUORUM_REQUIRED` enrolled reporters agree. Set it to 1
  when a single operator's validator is the only confirming reporter.
- Roll out consensus changes to the entire fleet together; automatic git updates should stay off.
- Give every validator the same registry edition, anchor, brief key, incentive hotkey, provider
  set, quorum, and LLM configuration.

## Verification and monitoring

```bash
source .venv/bin/activate
python -m pytest -q
./scripts/status.sh
pm2 logs herald_validator
curl -fsS https://herald-api.example/public/articles
```

Watch for `SUBMISSION_RESULT`, `INCENTIVE_WEIGHT`, `INCENTIVE_BURN` and `WEIGHT_VECTOR_REFUSED`
lines; [docs/validator.md](../../docs/validator.md) §8.8 lists every tag.

Before mainnet, rehearse with at least two validators and several submissions. Confirm identical
fingerprints, restart recovery, persistence checks, `INCENTIVE_WEIGHT` and `INCENTIVE_BURN` lines,
and a real `set_weights` extrinsic that carries only UID 0 and the incentive hotkey's UID.
