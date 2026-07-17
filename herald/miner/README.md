# Herald Miner

Herald miners are PR operators and outlet owners, not compute workers. A miner commits to pursuing
a funded brief at an approved outlet, gets a genuine editorial article published, attaches the
article URL, and serves the reveal to validators on Bittensor netuid 69.

Paid posts, advertorials, press-release wires, contributor programs classified as non-editorial,
and outlet-specific branded-content products are not eligible.

## Requirements

- Linux, Python 3.11 or 3.12
- A registered subnet-69 miner hotkey; its chain registration burn is the participation cost
- A public IP and open axon port
- Reliable storage for `claims.json`; it contains commitment nonces and must remain private

Install the project and prepare configuration:

```bash
./scripts/setup_env.sh
cp herald/miner/.env.example .env
```

Set `WALLET_NAME`, `HOTKEY_NAME`, `NETUID=69`, and a routable `AXON_EXTERNAL_IP`. Keep automatic
updates disabled so a process restart cannot unexpectedly change protocol behavior.

## Commit, publish, claim

List open briefs:

```bash
source ../venv_herald/bin/activate
python -m herald.miner.cli briefs
```

Commit before the article exists:

```bash
python -m herald.miner.cli commit \
  --brief <BRIEF_ID> \
  --outlet <OUTLET_ID> \
  --version 1 \
  --netuid 69 \
  --network finney \
  --wallet-name herald \
  --hotkey miner \
  --text-file draft.txt
```

Attribution evidence affects both winner selection and payout:

- `--text-file draft.txt` or `--quote ...`: level 2, full multiplier when the committed text is
  found in the published article.
- `--author ... --window YYYY-MM-DD:YYYY-MM-DD`: level 1 when the byline and tight publication
  window match.
- No evidence: level 0, reduced multiplier.

Evidence is hashed into the commitment and revealed only after publication. Do not use public
brief copy as evidence. The compatibility `bond_atto` field is written as zero automatically;
miners do not configure or escrow a Herald bond.

Once the editorial article is live:

```bash
python -m herald.miner.cli claim \
  --commit <ONCHAIN_VALUE> \
  --url https://approved-outlet.example/article
```

The CLI fetches and stores an extracted article snapshot. Validators anchor that snapshot against
their own fetch so page variants do not change the content verdict. For a bot-walled article, pass
`--snapshot-file`; use `--no-snapshot` only when you understand the reduced verifiability.

## Serve claims

Start the miner with PM2:

```bash
HERALD_MINER_ENV_FILE=/secure/config/miner.env ./scripts/run_miner.sh
pm2 logs herald_miner
```

Or with Compose:

```bash
MINER_ENV_FILE=/secure/config/miner.env docker compose up -d --build miner
docker compose logs -f miner
```

Compose stores claims in the `miner_data` volume and wallet/runtime data in `miner_wallet`.

The announced axon IP must be publicly routable. A loopback or container-private address will be
rejected by the chain or unreachable by validators. Configure:

```dotenv
PORT=8091
AXON_EXTERNAL_IP=<PUBLIC_IP>
AXON_EXTERNAL_PORT=8091
```

Open the public port in the host firewall and cloud security group.

## Claim-store safety

The claim store is written atomically with mode `0600`, but the operator remains responsible for
backup and filesystem permissions. Losing a nonce makes its on-chain commitment unrevealable.

Bittensor currently provides one commitment slot per hotkey. Do not overwrite an active placement
commit with a new placement or dispute until validators have accepted the reveal and started its
vesting entry.

To import dashboard-created reveals from the token-gated board:

```bash
python -m herald.miner.cli pull-reveals \
  --url https://herald-api.example \
  --token "$HERALD_REVEALS_TOKEN"
```

Never expose the reveals token or claim-store contents to a public browser. They contain the
nonces that open commitments.

## Why a claim may not pay

- The article predates the commitment or lacks a verifiable publication timestamp.
- The URL is outside the committed outlet or registry edition.
- The article is paid, sponsored, advertorial, or no longer live.
- The article does not match an open funded brief.
- A stronger-evidence or earlier valid claimant won the article or outlet/brief slot.
- The axon is unreachable or serving from a different hotkey.
- The article's client reward pool is exhausted.

Rewards vest over time. Removing an article or converting it to sponsored content can claw back
the remaining vest and temporarily zero the hotkey's other Herald rewards.
