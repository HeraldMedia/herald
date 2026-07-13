import os
import sys
from dotenv import load_dotenv
from pathlib import Path
import bittensor as bt

env_path = Path(__file__).parents[1] / '.env'
load_dotenv(dotenv_path=env_path)

# Cache Configuration
CACHE_ROOT = Path(__file__).resolve().parents[2] / "cache"
CACHE_DIRS = {
    "openai": os.path.join(CACHE_ROOT, "openai"),
    "briefs": os.path.join(CACHE_ROOT, "briefs"),
    "youtube_search": os.path.join(CACHE_ROOT, "youtube_search"),
    "minutes_revenue_ratio": os.path.join(CACHE_ROOT, "minutes_revenue_ratio"),
    # Herald (news placement) caches
    "web_fetch": os.path.join(CACHE_ROOT, "web_fetch"),
    "search": os.path.join(CACHE_ROOT, "search"),
    "news_snapshots": os.path.join(CACHE_ROOT, "news_snapshots"),
    "news_persistence": os.path.join(CACHE_ROOT, "news_persistence"),
    "herald_ledger": os.path.join(CACHE_ROOT, "herald_ledger"),
    "registry": os.path.join(CACHE_ROOT, "registry"),
}

# Cache expiry times (in seconds)
YOUTUBE_SEARCH_CACHE_EXPIRY = 12 * 60 * 60  # 12 hours
OPENAI_CACHE_EXPIRY = 3 * 24 * 60 * 60  # 3 days

__version__ = "2.6.1"

# required
HERALD_API_URL = os.getenv('HERALD_API_URL', 'https://herald-api.herald.network')
HERALD_BRIEFS_ENDPOINT = os.getenv('HERALD_BRIEFS_ENDPOINT', f"{HERALD_API_URL}/api/v2/validator/briefs")
# Ed25519 pubkey the validator uses to verify the brief payload's operator signature (so a brief's
# reward_pool + kind are attributable, not trust-the-endpoint). Unset = unsigned mode.
HERALD_BRIEFS_PUBKEY = os.getenv('HERALD_BRIEFS_PUBKEY')

# subnet mechanism configuration
MECHID = int(os.getenv('MECHID', '0'))

# new publishing configuration
ENABLE_DATA_PUBLISH = os.getenv('ENABLE_DATA_PUBLISH', 'False').lower() == 'true'
DATA_CLIENT_URL = os.getenv('DATA_CLIENT_URL', 'http://44.254.20.95')
YOUTUBE_SUBMIT_ENDPOINT = f"{DATA_CLIENT_URL}:7999/api/v1/youtube/submit"
WEIGHT_CORRECTIONS_ENDPOINT = f"{DATA_CLIENT_URL}:7999/api/v1/weight-corrections"

RAPID_API_KEY = os.getenv('RAPID_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
CHUTES_API_KEY = os.getenv('CHUTES_API_KEY')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
WANDB_API_KEY = os.getenv('WANDB_API_KEY')
WANDB_PROJECT = os.getenv('WANDB_PROJECT', 'herald_vali_logs')

# LLM Provider selection: "chutes" or "openrouter"
LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'chutes').lower()


# optional
DISABLE_LLM_CACHING = os.getenv('DISABLE_LLM_CACHING', 'False').lower() == 'true'

# Only run LLM checks on videos that pass all other checks
ECO_MODE = os.getenv('ECO_MODE', 'True').lower() == 'true'

# Disable prompt injection checking (saves 15-28s per request)
DISABLE_PROMPT_INJECTION = os.getenv('DISABLE_PROMPT_INJECTION', 'True').lower() == 'true'

# youtube scoring
YT_LOOKBACK = 90
YT_ROLLING_WINDOW = 7
YT_SCORING_WINDOW = 14
YT_REWARD_DELAY = 3
YT_VIDEO_RELEASE_BUFFER = 3
YT_MAX_VIDEOS = 75

YT_SCALING_FACTOR_DEDICATED = 1800
YT_SCALING_FACTOR_AD_READ = 400
YT_MIN_EMISSIONS = 0

# curve-based scoring
YT_NON_YPP_REVENUE_MULTIPLIER = 0.00005
YT_CURVE_DAMPENING_FACTOR = 0.1
YT_LIFETIME_DEDUCTION = 100
YT_LIFETIME_DEDUCTION_AD_READ = 25

# score capping
YT_SCORE_CAP_START_DAYS = 60  # T-60 days
YT_SCORE_CAP_END_DAYS = 30    # T-30 days

# youtube channel
YT_MIN_CHANNEL_AGE = 21
YT_MIN_SUBS = 100
YT_MAX_SUBS = 500000
YT_MIN_MINS_WATCHED = 1000
YT_MIN_CHANNEL_RETENTION = 10

# acceptance filter
YT_MIN_ALPHA_STAKE_THRESHOLD = 1000

# transcript api
TRANSCRIPT_MAX_RETRY = 10

# transcript maximum length in characters
TRANSCRIPT_MAX_LENGTH = 250000

# validation cycle (env-tunable so a local test can score every few seconds; prod defaults unchanged)
VALIDATOR_WAIT = int(os.getenv('HERALD_VALIDATOR_WAIT', '60'))  # seconds between forward passes
VALIDATOR_STEPS_INTERVAL = int(os.getenv('HERALD_VALIDATOR_STEPS_INTERVAL', '240'))  # score every Nth step

# synapse limits
MAX_ACCOUNTS_PER_SYNAPSE = 1000
CREDENTIAL_BATCH_SIZE = 8

DISCRETE_MODE = True

# subnet treasury
SUBNET_TREASURY_PERCENTAGE = 0
SUBNET_TREASURY_UID = int(os.getenv('SUBNET_TREASURY_UID', '106'))

# ── Herald (verified media placement) ──────────────────────────────────────
# Commit-ordering granularity in blocks (~12s/block -> ~72 min). Attribution ordering ONLY.
EPOCH_LEN = int(os.getenv('HERALD_EPOCH_LEN', '360'))
# Evaluation epoch in blocks (~12s/block -> ~1 day): drives vesting installments, persistence
# re-checks, slash cooldowns and dispute windows. VEST_EPOCHS installments ≈ VEST_EPOCHS days.
VEST_EPOCH_LEN = int(os.getenv('HERALD_VEST_EPOCH_LEN', '7200'))
# Lag the evaluation epoch behind the chain tip so a validator briefly ahead of finality
# doesn't advance early (reduces, not eliminates, epoch-boundary weight skew).
HERALD_EPOCH_LAG = int(os.getenv('HERALD_EPOCH_LAG', '10'))

SERPAPI_API_KEY = os.getenv('SERPAPI_API_KEY')
SCRAPINGBEE_API_KEY = os.getenv('SCRAPINGBEE_API_KEY')
BRAVE_API_KEY = os.getenv('BRAVE_API_KEY')
# Per-outlet fetch strategy (registry `fetch` field): "proxy" outlets need SCRAPINGBEE_API_KEY;
# "api:nyt" outlets need HERALD_NYT_API_KEY. A validator missing the key for an outlet's strategy
# can't verify that outlet (fail-closed) and will fork from validators that have it — so these keys
# are consensus-affecting (surfaced in the fingerprint) and must be provisioned fleet-wide.
HERALD_NYT_API_KEY = os.getenv('HERALD_NYT_API_KEY')
# ── Local-sim testing hooks — all default to the REAL endpoints/behaviour, so production is
# unchanged. Point these at a localhost simulator to run the whole pipeline offline. NEVER set the
# overrides (or HERALD_ALLOW_LOCAL_FETCH) in production. They are deployment infra, not scoring
# params, so they are intentionally NOT part of the consensus fingerprint.
HERALD_NYT_API_BASE = os.getenv('HERALD_NYT_API_BASE', 'https://api.nytimes.com/svc/search/v2/articlesearch.json')
HERALD_SCRAPINGBEE_BASE = os.getenv('HERALD_SCRAPINGBEE_BASE', 'https://app.scrapingbee.com/api/v1')
HERALD_SERPAPI_BASE = os.getenv('HERALD_SERPAPI_BASE', 'https://serpapi.com/search.json')
HERALD_BRAVE_BASE = os.getenv('HERALD_BRAVE_BASE', 'https://api.search.brave.com/res/v1/web/search')
# Allow the SSRF-guarded direct fetch to reach loopback/private hosts (for a localhost sim only).
HERALD_ALLOW_LOCAL_FETCH = os.getenv('HERALD_ALLOW_LOCAL_FETCH', 'false').lower() in ('1', 'true', 'yes')
HERALD_SEARCH_TOP_N = int(os.getenv('HERALD_SEARCH_TOP_N', '20'))
HERALD_MIN_BODY_BYTES = int(os.getenv('HERALD_MIN_BODY_BYTES', '500'))
HERALD_MAX_BODY_BYTES = int(os.getenv('HERALD_MAX_BODY_BYTES', str(5_000_000)))
# Fallback model id for the LLM judgement tier (the provider's pinned model is preferred).
HERALD_REF_MODEL_ID = os.getenv('HERALD_REF_MODEL_ID', '')
# The LLM judgement tier must be enabled identically across validators, or weights diverge.
HERALD_USE_LLM_JUDGE = os.getenv('HERALD_USE_LLM_JUDGE', 'false').lower() == 'true'
# Quorum: number of providers that must agree before a verdict is accepted (clamped
# to the number configured). Defaults to 1 (single provider).
HERALD_QUORUM_THRESHOLD = int(os.getenv('HERALD_QUORUM_THRESHOLD', '1'))

HERALD_BASE_PAYOUT_USD = float(os.getenv('HERALD_BASE_PAYOUT_USD', '500'))
HERALD_TIER_MULTIPLIER = {1: 1.0, 2: 0.6, 3: 0.2}  # publications sheet weights 5/3/1, normalized
# Payout multiplier for a live-but-not-indexed article. Search results vary per validator
# (IP/region/rate limits); a floor of 0 turns that variance into a pay/no-pay fork that splits
# vesting ledgers for 30 days. 0.5 softens the cliff to a 2x spread — with SEVERAL validators
# this is a consensus-stability parameter, not just pricing.
HERALD_NO_SEARCH_FLOOR = float(os.getenv('HERALD_NO_SEARCH_FLOOR', '0.5'))

# ── Attribution evidence (see evidence.py/textmatch.py) — CONSENSUS-CRITICAL: set identically on
# every validator or the same claim pays differently and weights diverge. Payout multiplier per
# evidence level: 2 = committed text found in the article, 1 = committed byline + tight publish
# window both match, 0 = bare commit (ratchet L0 to 0 once miners adopt evidence).
HERALD_ATTR_MULT = {
    2: float(os.getenv('HERALD_ATTR_MULT_L2', '1.0')),
    1: float(os.getenv('HERALD_ATTR_MULT_L1', '0.7')),
    0: float(os.getenv('HERALD_ATTR_MULT_L0', '0.3')),
}
# Level 2 gates: min evidence-text length and shingle-containment threshold vs the article.
HERALD_ATTR_MIN_TEXT_WORDS = int(os.getenv('HERALD_ATTR_MIN_TEXT_WORDS', '8'))
HERALD_ATTR_TEXT_THRESHOLD = float(os.getenv('HERALD_ATTR_TEXT_THRESHOLD', '0.6'))
# Level 1 gate: the committed publish window may span at most this many days.
HERALD_ATTR_MAX_WINDOW_DAYS = int(os.getenv('HERALD_ATTR_MAX_WINDOW_DAYS', '7'))
# Snapshot anchoring: a claim's miner-supplied page snapshot must reach this shingle containment
# vs the validator's own fetch; content checks then run on the identical snapshot bytes so all
# validators agree. Below the anchor -> reject this pass (re-fetched next epoch).
HERALD_SNAPSHOT_ANCHOR = float(os.getenv('HERALD_SNAPSHOT_ANCHOR', '0.5'))
HERALD_MAX_ARTICLES_PER_MINER = int(os.getenv('HERALD_MAX_ARTICLES_PER_MINER', '200'))

HERALD_MIN_ALPHA_STAKE_THRESHOLD = float(os.getenv('HERALD_MIN_ALPHA_STAKE_THRESHOLD', '0'))
# A claim's committed bond must cover SLASH_MULTIPLIER x its expected reward, converted to
# alpha via HERALD_BOND_ALPHA_PER_USD (set near 1/alpha_price after the pilot).
SLASH_MULTIPLIER = float(os.getenv('HERALD_SLASH_MULTIPLIER', '1.5'))
HERALD_BOND_ALPHA_PER_USD = float(os.getenv('HERALD_BOND_ALPHA_PER_USD', '1.0'))

# Vesting over the persistence window, and slash cooldown (in evaluation epochs).
VEST_EPOCHS = int(os.getenv('HERALD_VEST_EPOCHS', '30'))
SLASH_COOLDOWN_EPOCHS = int(os.getenv('HERALD_SLASH_COOLDOWN_EPOCHS', '7'))
# Require this many consecutive confirmed-dead epochs before clawback+slash, so a transient
# 404/geo-block (or a stray "sponsored" string) doesn't slash an honest miner.
HERALD_DEAD_CONFIRM_EPOCHS = int(os.getenv('HERALD_DEAD_CONFIRM_EPOCHS', '2'))
# Expire an article still vesting long after its window (bounds state; terminates held entries).
HERALD_VEST_GRACE_EPOCHS = int(os.getenv('HERALD_VEST_GRACE_EPOCHS', '30'))

# ── Disputes (escalated re-scrutiny of a placement; see DISPUTE_DESIGN.md) ──────
# A dispute (on-chain HRLDDIS commit) forces the pinned judge on a placement; the existing
# persistence verdict decides it. Resolution runs the judge, so it is DISABLED unless
# HERALD_REF_MODEL_ID is pinned identically across validators (a mixed fleet would diverge).
# Share of a slashed miner's forfeited (otherwise-burned) vesting paid to the disputer's UID.
HERALD_DISPUTE_REWARD_FRACTION = float(os.getenv('HERALD_DISPUTE_REWARD_FRACTION', '0.5'))
# Epochs a dispute stays open; a still-alive article at the window's close rejects it and slashes
# the disputer. Kept >= HERALD_DEAD_CONFIRM_EPOCHS so an upheld dispute has time to confirm dead.
HERALD_DISPUTE_WINDOW_EPOCHS = int(os.getenv('HERALD_DISPUTE_WINDOW_EPOCHS', '4'))

# ── Client-funded briefs (prepaid reward pool + DSV treasury; see FUNDING_DESIGN.md) ──
# A client brief is paid from its prepaid `reward_pool` (USD), funded when the client pays alpha/TAO
# into the DSV treasury; the standing brief pays from emissions. The operator confirms the payment and
# signs the brief funded (the trusted funded signal). Treasury address for that settlement:
HERALD_TREASURY_COLDKEY = os.getenv('HERALD_TREASURY_COLDKEY', '')
# Max plausible gap between commit and publication; a far-future date is rejected as implausible.
HERALD_MAX_PLACEMENT_DAYS = int(os.getenv('HERALD_MAX_PLACEMENT_DAYS', '90'))

# Emissions track USD value: miners earn their share of total daily emissions
# (valued in USD); the unclaimed remainder is burned to the burn UID.
HERALD_TOTAL_DAILY_USD = float(os.getenv('HERALD_TOTAL_DAILY_USD', '1000'))
SUBNET_BURN_UID = int(os.getenv('HERALD_BURN_UID', '0'))

# Log out all non-sensitive config variables
bt.logging.info(f"HERALD_BRIEFS_ENDPOINT: {HERALD_BRIEFS_ENDPOINT}")
bt.logging.info(f"YOUTUBE_SUBMIT_ENDPOINT: {YOUTUBE_SUBMIT_ENDPOINT}")
bt.logging.info(f"ENABLE_DATA_PUBLISH: {ENABLE_DATA_PUBLISH}")
bt.logging.info(f"WEIGHT_CORRECTIONS_ENDPOINT: {WEIGHT_CORRECTIONS_ENDPOINT}")
bt.logging.info(f"DISABLE_LLM_CACHING: {DISABLE_LLM_CACHING}")
bt.logging.info(f"LLM_PROVIDER: {LLM_PROVIDER}")
bt.logging.info(f"ECO_MODE: {ECO_MODE}")
bt.logging.info(f"DISABLE_PROMPT_INJECTION: {DISABLE_PROMPT_INJECTION}")
bt.logging.info(f"YT_MIN_SUBS: {YT_MIN_SUBS}")
bt.logging.info(f"YT_MAX_SUBS: {YT_MAX_SUBS}")
bt.logging.info(f"YT_MIN_CHANNEL_AGE: {YT_MIN_CHANNEL_AGE}")
bt.logging.info(f"YT_MIN_MINS_WATCHED: {YT_MIN_MINS_WATCHED}")
bt.logging.info(f"YT_MIN_CHANNEL_RETENTION: {YT_MIN_CHANNEL_RETENTION}")
bt.logging.info(f"YT_MAX_VIDEOS: {YT_MAX_VIDEOS}")
bt.logging.info(f"YT_MIN_ALPHA_STAKE_THRESHOLD: {YT_MIN_ALPHA_STAKE_THRESHOLD}")
bt.logging.info(f"YT_VIDEO_RELEASE_BUFFER: {YT_VIDEO_RELEASE_BUFFER}")
bt.logging.info(f"YT_ROLLING_WINDOW: {YT_ROLLING_WINDOW}")
bt.logging.info(f"YT_SCORING_WINDOW: {YT_SCORING_WINDOW}")
bt.logging.info(f"YT_REWARD_DELAY: {YT_REWARD_DELAY}")
bt.logging.info(f"YT_LOOKBACK: {YT_LOOKBACK}")
bt.logging.info(f"YT_NON_YPP_REVENUE_MULTIPLIER: {YT_NON_YPP_REVENUE_MULTIPLIER}")
bt.logging.info(f"YT_CURVE_DAMPENING_FACTOR: {YT_CURVE_DAMPENING_FACTOR}")
bt.logging.info(f"YT_SCORE_CAP_START_DAYS: {YT_SCORE_CAP_START_DAYS}")
bt.logging.info(f"YT_SCORE_CAP_END_DAYS: {YT_SCORE_CAP_END_DAYS}")
bt.logging.info(f"YT_LIFETIME_DEDUCTION: {YT_LIFETIME_DEDUCTION}")
bt.logging.info(f"YT_LIFETIME_DEDUCTION_AD_READ: {YT_LIFETIME_DEDUCTION_AD_READ}")
bt.logging.info(f"TRANSCRIPT_MAX_RETRY: {TRANSCRIPT_MAX_RETRY}")
bt.logging.info(f"TRANSCRIPT_MAX_LENGTH: {TRANSCRIPT_MAX_LENGTH}")
bt.logging.info(f"VALIDATOR_WAIT: {VALIDATOR_WAIT}")
bt.logging.info(f"VALIDATOR_STEPS_INTERVAL: {VALIDATOR_STEPS_INTERVAL}")
bt.logging.info(f"MAX_ACCOUNTS_PER_SYNAPSE: {MAX_ACCOUNTS_PER_SYNAPSE}")
bt.logging.info(f"CREDENTIAL_BATCH_SIZE: {CREDENTIAL_BATCH_SIZE}")
bt.logging.info(f"DISCRETE_MODE: {DISCRETE_MODE}")
bt.logging.info(f"SUBNET_TREASURY_PERCENTAGE: {SUBNET_TREASURY_PERCENTAGE}")
bt.logging.info(f"SUBNET_TREASURY_UID: {SUBNET_TREASURY_UID}")