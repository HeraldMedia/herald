"""Publish verified articles to the public results service (fire-and-forget)."""

import os

import bittensor as bt
import httpx


def publish_results(endpoint: str, items: list):
    if not endpoint or not items:
        return
    token = os.getenv("HERALD_RESULTS_TOKEN")
    headers = {"X-Results-Token": token} if token else {}
    for item in items:
        try:
            httpx.post(f"{endpoint}/results", json=item, timeout=5.0, headers=headers)
        except Exception as e:
            bt.logging.warning(f"Result publish failed: {e}")
