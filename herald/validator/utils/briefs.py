import requests
import bittensor as bt
from datetime import datetime, timezone, timedelta
from diskcache import Cache
import os
from threading import Lock
import atexit
from herald.validator.utils.config import HERALD_BRIEFS_ENDPOINT, VEST_EPOCHS, CACHE_DIRS
from herald.validator.utils.error_handling import log_and_raise_api_error
from herald.validator.news.signed_briefs import verify_briefs

class BriefsCache:
    _instance = None
    _lock = Lock()
    _cache: Cache = None
    _cache_dir = CACHE_DIRS["briefs"]

    @classmethod
    def initialize_cache(cls) -> None:
        """Initialize the cache if it hasn't been initialized yet."""
        if cls._cache is None:
            os.makedirs(cls._cache_dir, exist_ok=True)
            cls._cache = Cache(
                directory=cls._cache_dir,
                size_limit=1e9,  # 1GB
                disk_min_file_size=0,
                disk_pickle_protocol=4,
            )
            # Register cleanup on program exit
            atexit.register(cls.cleanup)

    @classmethod
    def cleanup(cls) -> None:
        """Clean up resources."""
        if cls._cache is not None:
            with cls._lock:
                if cls._cache is not None:
                    cls._cache.close()
                    cls._cache = None

    @classmethod
    def get_cache(cls) -> Cache:
        """Thread-safe cache access."""
        if cls._cache is None:
            cls.initialize_cache()
        return cls._cache

    def __del__(self):
        """Ensure cleanup on object destruction."""
        self.cleanup()

# Initialize cache
BriefsCache.initialize_cache()

def get_briefs(all: bool = False, now: datetime = None):
    """
    Fetches the briefs from the server.

    :param all: If True, returns all briefs without filtering; if False, only briefs in their
                active window. Standing briefs (kind == "standing", or no end_date) are always
                active. A client brief is active from start_date through end_date plus a
                persistence tail of VEST_EPOCHS days, so vesting re-checks keep running after
                the brief closes (assumes the default ~1-day HERALD_VEST_EPOCH_LEN).
    :param now: Time to evaluate the window against. Pass chain-derived time so validators agree
                at day boundaries; defaults to wall-clock UTC.
    :return: List of brief objects
    """
    cache = BriefsCache.get_cache()
    cache_key = f"briefs_{all}"

    try:
        # Always try to fetch from API first. The timeout matters: without it a hanging board
        # blocks the whole forward pass (the cache fallback only fires on a raised exception).
        response = requests.get(HERALD_BRIEFS_ENDPOINT, timeout=(5, 30))
        response.raise_for_status()
        # Verify the operator signature (when HERALD_BRIEFS_PUBKEY is set) BEFORE trusting any
        # brief's reward_pool/kind; a bad signature fails closed (raises, not served from cache).
        briefs_data = verify_briefs(response.json())

        briefs_list = briefs_data.get("items") or []
        bt.logging.info(f"Fetched {len(briefs_list)} briefs.")

        filtered_briefs = []
        if not all:
            current_date = (now or datetime.now(timezone.utc)).date()

            for brief in briefs_list:
                try:
                    if brief.get("kind") == "standing" or not brief.get("end_date"):
                        filtered_briefs.append(brief)  # always-open
                        continue
                    end_date = datetime.strptime(brief["end_date"], "%Y-%m-%d").date()
                    start = brief.get("start_date")
                    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else None
                    end_window = end_date + timedelta(days=VEST_EPOCHS)
                    if (start_date is None or start_date <= current_date) and current_date <= end_window:
                        filtered_briefs.append(brief)
                except Exception as e:
                    bt.logging.error(f"Error parsing dates for brief {brief.get('id', 'unknown')}: {e}")

            if not filtered_briefs:
                bt.logging.info("No briefs are in their active window.")
        else:
            filtered_briefs = briefs_list

        # Store the successful API response in cache
        cache.set(cache_key, filtered_briefs)
        return filtered_briefs

    except requests.exceptions.RequestException as e:
        # Try to return cached data if available
        cached_briefs = cache.get(cache_key)
        if cached_briefs is not None:
            bt.logging.warning("Using cached briefs due to API error")
            return cached_briefs
        
        # No cached data available - this is a real error
        log_and_raise_api_error(
            error=e,
            endpoint=HERALD_BRIEFS_ENDPOINT,
            context="Content briefs fetch"
        )