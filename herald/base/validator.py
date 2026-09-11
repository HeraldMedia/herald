import contextlib
import copy
import hashlib
import json
import logging
import os
import numpy as np
import asyncio
import argparse
import threading
import time
import bittensor as bt

from typing import List, Union
from traceback import print_exception
from urllib.parse import urlsplit

from herald.base.neuron import BaseNeuron
from herald.base.utils.weight_utils import (
    process_weights_for_netuid,
    convert_weights_and_uids_for_emit,
)
from herald.utils.config import add_validator_args

# Operational knobs for the pending weight-commit check. None of them is a consensus parameter.
WEIGHT_CHECK_ATTEMPTS_ENV = "HERALD_WEIGHT_CHECK_ATTEMPTS"
WEIGHT_CHECK_BACKOFF_ENV = "HERALD_WEIGHT_CHECK_BACKOFF_SECONDS"
WEIGHT_CHECK_FALLBACK_ENV = "HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT"
WEIGHT_SUPPRESSION_ALERT_ENV = "HERALD_WEIGHT_SUPPRESSION_ALERT_THRESHOLD"
WEIGHT_SUPPRESSION_ALERT_TAG = "WEIGHT_SUBMISSION_STALLED"


def _env_number(name: str, default, cast, minimum):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, cast(raw))
    except ValueError:
        bt.logging.warning(f"{name}={raw!r} is not a valid number; using {default}")
        return default


def _endpoint_for_logs(endpoint) -> str:
    """An RPC endpoint as logs may show it: scheme://host[:port] and nothing else.

    Paid RPC providers often carry an API key in the URL path, query or userinfo, and these lines
    reach container logs and CloudWatch.
    """
    text = str(endpoint or "").strip()
    if not text:
        return "unknown endpoint"
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "<unparseable endpoint>"
    if parts.netloc and host:
        return f"{parts.scheme}://{host}" + (f":{port}" if port else "")
    return text if text.isidentifier() else "<unparseable endpoint>"  # a network name like "finney"


def _scrubbed(error: BaseException, *endpoints) -> str:
    """The error text with every configured endpoint, and each secret-bearing part of one, redacted.

    Whole endpoints become their log-safe form, longest first, so an endpoint that is a prefix of
    another cannot split it. Then a URL endpoint's path, query, username or password that still
    appears on its own (an HTTP status line, a re-normalised URL) is replaced too. Parts shorter
    than six characters are left alone so ordinary words are not mangled.
    """
    text = str(error)
    raws = sorted({str(e).strip() for e in endpoints if e and str(e).strip()}, key=len, reverse=True)
    for raw in raws:
        text = text.replace(raw, _endpoint_for_logs(raw))
    parts = set()
    for raw in raws:
        try:
            split = urlsplit(raw)
            username, password = split.username, split.password
        except ValueError:
            continue
        if not split.netloc:
            continue  # a network name such as "finney" carries nothing to hide
        if split.query:
            parts.add(split.query)
            parts.add(f"{split.path}?{split.query}")
        parts.update(p for p in (split.path, username, password) if p)
    for part in sorted((p for p in parts if len(p) >= 6), key=len, reverse=True):
        text = text.replace(part, "<redacted>")
    return text


@contextlib.contextmanager
def _bittensor_debug_muted():
    """Hold bittensor's logger at INFO or above for the duration.

    Subtensor.__init__ logs its chain endpoint verbatim at DEBUG, and a fallback endpoint may carry a
    provider key in its URL. The previous level is restored exactly.
    """
    level = bt.logging.get_level()
    if level >= logging.INFO:
        yield
        return
    bt.logging.setLevel(logging.INFO)
    try:
        yield
    finally:
        bt.logging.setLevel(level)


class WeightCommitCheckError(RuntimeError):
    """No endpoint could say whether this hotkey has a weight commit pending reveal."""


class BaseValidatorNeuron(BaseNeuron):
    """
    Base class for Bittensor validators. Your validator should inherit from this class.
    """

    neuron_type: str = "ValidatorNeuron"

    @classmethod
    def add_args(cls, parser: argparse.ArgumentParser):
        super().add_args(parser)
        add_validator_args(cls, parser)

    def __init__(self, config=None):
        super().__init__(config=config)

        # Save a copy of the hotkeys to local memory.
        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)

        # Dendrite lets us send messages to other nodes (axons) in the network.
        self.dendrite = bt.Dendrite(wallet=self.wallet)
        bt.logging.info(f"Dendrite: {self.dendrite}")

        # Set up initial scoring weights for validation
        bt.logging.info("Building validation weights.")
        self.scores = np.zeros(self.metagraph.n, dtype=np.float32)

        # Restore before the initial sync: sync() always saves state, so loading afterward would
        # overwrite a valid checkpoint with zero scores on every restart.
        state_path = self.config.neuron.full_path + "/state.npz"
        if os.path.exists(state_path):
            self.load_state()

        # Init sync with the network. Updates the metagraph.
        self.sync()

        # Serve axon to enable external connections.
        if not self.config.neuron.axon_off:
            self.serve_axon()
        else:
            bt.logging.warning("axon off, not serving ip to chain.")

        # Create asyncio event loop to manage async tasks.
        self.loop = asyncio.get_event_loop()

        # Instantiate runners
        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: Union[threading.Thread, None] = None
        self.lock = asyncio.Lock()

    def serve_axon(self):
        """Serve axon to enable external connections."""

        bt.logging.info("serving ip to chain...")
        try:
            self.axon = bt.Axon(wallet=self.wallet, config=self.config)

            try:
                self.subtensor.serve_axon(
                    netuid=self.config.netuid,
                    axon=self.axon,
                )
                bt.logging.info(
                    f"Running validator {self.axon} on network: {self.config.subtensor.chain_endpoint} with netuid: {self.config.netuid}"
                )
            except Exception as e:
                bt.logging.error(f"Failed to serve Axon with exception: {e}")
                pass

        except Exception as e:
            bt.logging.error(
                f"Failed to create Axon initialize with exception: {e}"
            )
            pass

    async def concurrent_forward(self):
        coroutines = [
            self.forward()
            for _ in range(self.config.neuron.num_concurrent_forwards)
        ]
        await asyncio.gather(*coroutines)

    def run(self):
        """
        Initiates and manages the main loop for the miner on the Bittensor network. The main loop handles graceful shutdown on keyboard interrupts and logs unforeseen errors.

        This function performs the following primary tasks:
        1. Check for registration on the Bittensor network.
        2. Continuously forwards queries to the miners on the network, rewarding their responses and updating the scores accordingly.
        3. Periodically resynchronizes with the chain; updating the metagraph with the latest network state and setting weights.

        The essence of the validator's operations is in the forward function, which is called every step. The forward function is responsible for querying the network and scoring the responses.

        Note:
            - The function leverages the global configurations set during the initialization of the miner.
            - The miner's axon serves as its interface to the Bittensor network, handling incoming and outgoing requests.

        Raises:
            KeyboardInterrupt: If the miner is stopped by a manual interruption.
            Exception: For unforeseen errors during the miner's operation, which are logged for diagnosis.
        """

        # Check that validator is registered on the network.
        self.sync()

        bt.logging.info(f"Validator starting at block: {self.block}")

        # This loop maintains the validator's operations until intentionally stopped.
        while True:
            try:
                bt.logging.info(f"step({self.step}) block({self.block})")

                # Run multiple forwards concurrently.
                self.loop.run_until_complete(self.concurrent_forward())

                # Check if we should exit.
                if self.should_exit:
                    break

                # Sync metagraph and potentially set weights.
                try:
                    self.sync()
                except Exception as e:
                    bt.logging.warning(f"Sync operation failed, continuing validation cycle: {e}")

                self.step += 1

            # If someone intentionally stops the validator, it'll safely terminate operations.
            except KeyboardInterrupt:
                self.axon.stop()
                bt.logging.success("Validator killed by keyboard interrupt.")
                exit()

            # In case of unforeseen errors, the validator will log the error and continue operations.
            except Exception as err:
                bt.logging.error(f"Error during validation: {str(err)}")
                bt.logging.debug(
                    str(print_exception(type(err), err, err.__traceback__))
                )
                # Increment step even on error to avoid getting stuck
                self.step += 1
                # Wait before retrying to avoid rapid failure loops
                time.sleep(30)
                # Continue the main loop instead of exiting
                continue

    def run_in_background_thread(self):
        """
        Starts the validator's operations in a background thread upon entering the context.
        This method facilitates the use of the validator in a 'with' statement.
        """
        if not self.is_running:
            bt.logging.debug("Starting validator in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Started")

    def stop_run_thread(self):
        """
        Stops the validator's operations that are running in the background thread.
        """
        if self.is_running:
            bt.logging.debug("Stopping validator in background thread.")
            self.should_exit = True
            self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def __enter__(self):
        self.run_in_background_thread()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Stops the validator's background operations upon exiting the context.
        This method facilitates the use of the validator in a 'with' statement.

        Args:
            exc_type: The type of the exception that caused the context to be exited.
                      None if the context was exited without an exception.
            exc_value: The instance of the exception that caused the context to be exited.
                       None if the context was exited without an exception.
            traceback: A traceback object encoding the stack trace.
                       None if the context was exited without an exception.
        """
        if self.is_running:
            bt.logging.debug("Stopping validator in background thread.")
            self.should_exit = True
            self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def _has_pending_weight_commit(self, subtensor=None) -> bool:
        """True when this hotkey has a timelocked weight commit awaiting reveal.

        Reads the chain head (``block=None``). bittensor 10.5 resolves that to a fresh
        ``chain_getHead`` on every call, so a retry re-reads at the new head rather than asking a
        load-balanced RPC node again for a block hash it has not imported ("UnknownBlock: Header
        was not found in the database"). The per-block cache covers the primary connection only.
        """
        hotkey = self.wallet.hotkey.ss58_address
        if subtensor is not None:
            commits = subtensor.get_timelocked_weight_commits(self.config.netuid)
            return any(commit[0] == hotkey for commit in commits)

        block = int(self.block)
        cached = getattr(self, "_pending_weight_commit_cache", None)
        if cached is not None and cached[0] == block:
            return cached[1]

        commits = self.subtensor.get_timelocked_weight_commits(self.config.netuid)
        pending = any(commit[0] == hotkey for commit in commits)
        self._pending_weight_commit_cache = (block, pending)
        return pending

    def _weight_commit_pending(self, subtensor=None) -> bool:
        """One complete check: commit-reveal is enabled AND this hotkey has a commit pending."""
        target = self.subtensor if subtensor is None else subtensor
        if not target.commit_reveal_enabled(self.config.netuid):
            return False
        return self._has_pending_weight_commit(subtensor)

    def _raw_chain_endpoint(self):
        return getattr(self.subtensor, "chain_endpoint", None) or getattr(
            getattr(self.config, "subtensor", None), "chain_endpoint", None
        )

    def _chain_endpoint(self) -> str:
        """The primary endpoint as logs show it (see _endpoint_for_logs)."""
        return _endpoint_for_logs(self._raw_chain_endpoint())

    def _check_pending_weight_commit(self) -> bool:
        """Answer "is a weight commit pending?", or raise WeightCommitCheckError.

        Up to HERALD_WEIGHT_CHECK_ATTEMPTS reads on the primary connection with doubling backoff
        from HERALD_WEIGHT_CHECK_BACKOFF_SECONDS, then one read through
        HERALD_WEIGHT_CHECK_FALLBACK_ENDPOINT when that is set. Retries only re-ask the question;
        a "pending" answer from any endpoint still blocks submission exactly as before.
        """
        attempts = _env_number(WEIGHT_CHECK_ATTEMPTS_ENV, 3, int, 1)
        delay = _env_number(WEIGHT_CHECK_BACKOFF_ENV, 4.0, float, 0.0)
        raw_primary = self._raw_chain_endpoint()
        primary = _endpoint_for_logs(raw_primary)
        fallback = os.getenv(WEIGHT_CHECK_FALLBACK_ENV, "").strip()
        failures = []
        for attempt in range(1, attempts + 1):
            try:
                return self._weight_commit_pending()
            except Exception as e:
                reason = _scrubbed(e, raw_primary, fallback)
                failures.append(
                    f"{primary} attempt {attempt}/{attempts}: {type(e).__name__}: {reason}"
                )
                if attempt < attempts:
                    bt.logging.warning(
                        f"Pending weight-commit check failed on {primary} "
                        f"(attempt {attempt}/{attempts}): {reason}; retrying at the chain head in "
                        f"{delay:g}s"
                    )
                    time.sleep(delay)
                    delay *= 2
        if fallback:
            shown = _endpoint_for_logs(fallback)
            try:
                pending = self._weight_commit_pending(self._weight_check_fallback_subtensor(fallback))
            except Exception as e:
                self._drop_weight_check_fallback()
                failures.append(
                    f"{shown} (fallback): {type(e).__name__}: {_scrubbed(e, raw_primary, fallback)}"
                )
            else:
                bt.logging.warning(
                    f"Pending weight-commit check failed on {primary} and was answered by the "
                    f"fallback endpoint {shown}"
                )
                return pending
        raise WeightCommitCheckError("; ".join(failures))

    def _weight_check_fallback_subtensor(self, endpoint: str):
        cached = getattr(self, "_weight_check_fallback", None)
        if cached is not None and cached[0] == endpoint:
            return cached[1]
        self._drop_weight_check_fallback()
        with _bittensor_debug_muted():
            subtensor = bt.Subtensor(network=endpoint)  # read-only use: two storage queries
        self._weight_check_fallback = (endpoint, subtensor)
        return subtensor

    def _drop_weight_check_fallback(self):
        cached = getattr(self, "_weight_check_fallback", None)
        self._weight_check_fallback = None
        if cached is not None:
            try:
                cached[1].close()
            except Exception:
                pass

    def _has_weights_to_submit(self) -> bool:
        """Subclass hook: False when there is locally nothing to submit this step.

        Asked after the chain-age gate and before the pending-commit check. The gates are ANDed, so
        this changes no submission; it keeps the check's retries, backoff sleeps, suppression count
        and WEIGHT_SUBMISSION_STALLED alert for steps that would really submit.
        """
        return True

    def should_set_weights(self) -> bool:
        if not super().should_set_weights():
            return False
        if not self._has_weights_to_submit():
            # Nothing to submit ends a stall window: failures from a window that closed without a
            # submission must not count toward the alert in the next one.
            self._suppressed_weight_submissions = 0
            return False
        try:
            pending = self._check_pending_weight_commit()
        except Exception as e:
            # Unknown commit state. Submitting anyway could put a second commit on chain while one
            # is still pending reveal, so stay suppressed, but loudly: ERROR, counted, alerting.
            self._note_suppressed_weight_submission(e)
            return False
        self._note_weight_check_recovered()
        if pending:
            bt.logging.info(
                "Weight commitment pending automatic reveal; skipping resubmission"
            )
            return False
        return True

    def _note_suppressed_weight_submission(self, error: Exception):
        count = getattr(self, "_suppressed_weight_submissions", 0) + 1
        self._suppressed_weight_submissions = count
        bt.logging.error(
            f"Weight submission suppressed on netuid {self.config.netuid}: could not determine "
            f"whether a weight commit is pending (endpoint {self._chain_endpoint()}; {error}). "
            f"Not submitting blind, since a second commit while one may be pending is a "
            f"chain-level duplicate. Consecutive suppressed submissions: {count}"
        )
        threshold = _env_number(WEIGHT_SUPPRESSION_ALERT_ENV, 3, int, 1)
        if count >= threshold:
            bt.logging.error(
                f"{WEIGHT_SUPPRESSION_ALERT_TAG}: {count} consecutive weight submissions suppressed "
                f"(alert threshold {threshold}) because the pending-commit check keeps failing on "
                f"{self._chain_endpoint()}. This validator is not setting weights. Set "
                f"{WEIGHT_CHECK_FALLBACK_ENV} to a second finney node (used only for this check) "
                f"and recreate the container."
            )

    def _note_weight_check_recovered(self):
        count = getattr(self, "_suppressed_weight_submissions", 0)
        if count:
            bt.logging.warning(
                f"Pending weight-commit check recovered after {count} suppressed weight "
                f"submission(s)"
            )
        self._suppressed_weight_submissions = 0

    def set_weights(self):
        """
        Sets the validator weights to the metagraph hotkeys based on the scores it has received from the miners. The weights determine the trust and incentive level the validator assigns to miner nodes on the network.
        """

        # Check if self.scores contains any NaN values and log a warning if it does.
        if np.isnan(self.scores).any():
            bt.logging.warning(
                f"Scores contain NaN values. This may be due to a lack of responses from miners, or a bug in your reward functions."
            )

        # Calculate the average reward for each uid across non-zero values.
        # Replace any NaN values with 0.
        # Compute the norm of the scores
        norm = np.linalg.norm(self.scores, ord=1, axis=0, keepdims=True)

        if np.any(norm == 0):
            bt.logging.info("No rewarded miners in the current epoch; skipping weight submission")
            return False

        # Check if the norm contains NaN values
        if np.isnan(norm).any():
            norm = np.ones_like(norm)

        # Compute raw_weights safely
        raw_weights = self.scores / norm

        bt.logging.debug("raw_weights", raw_weights)
        bt.logging.debug("raw_weight_uids", str(self.metagraph.uids.tolist()))
        # Process the raw weights to final_weights via subtensor limitations.
        (
            processed_weight_uids,
            processed_weights,
        ) = process_weights_for_netuid(
            uids=self.metagraph.uids,
            weights=raw_weights,
            netuid=self.config.netuid,
            subtensor=self.subtensor,
            metagraph=self.metagraph,
        )
        bt.logging.debug("processed_weights", processed_weights)
        bt.logging.debug("processed_weight_uids", processed_weight_uids)

        # Convert to uint16 weights and uids.
        (
            uint_uids,
            uint_weights,
        ) = convert_weights_and_uids_for_emit(
            uids=processed_weight_uids, weights=processed_weights
        )
        bt.logging.debug("uint_weights", uint_weights)
        bt.logging.debug("uint_uids", uint_uids)
        submitted_vector = [[int(uid), int(weight)] for uid, weight in zip(uint_uids, uint_weights)]
        self._last_submitted_weight_vector = submitted_vector
        self._last_submitted_weight_vector_hash = hashlib.sha256(
            json.dumps(submitted_vector, separators=(",", ":")).encode()
        ).hexdigest()

        # Set the weights on chain via our subtensor connection.
        commit_reveal = self.subtensor.commit_reveal_enabled(self.config.netuid)
        result, msg = self.subtensor.set_weights(
            wallet=self.wallet,
            netuid=self.config.netuid,
            uids=uint_uids,
            weights=uint_weights,
            wait_for_finalization=False,
            wait_for_inclusion=True,
            wait_for_revealed_execution=False,
            version_key=self.spec_version,
        )
        if result is True:
            self._pending_weight_commit_cache = None
            if commit_reveal:
                bt.logging.info(
                    "Weight commitment included on chain; pending automatic reveal"
                )
            else:
                bt.logging.info("set_weights on chain successfully!")
            return True
        else:
            bt.logging.error("set_weights failed", msg)
            return False

    def resync_metagraph(self):
        """Resyncs the metagraph and updates the hotkeys and moving averages based on the new metagraph."""
        bt.logging.info("resync_metagraph()")

        # Copies state of metagraph before syncing.
        previous_metagraph = copy.deepcopy(self.metagraph)

        # Sync the metagraph.
        self.sync_core_metagraph()

        # Check if the metagraph axon info has changed.
        if previous_metagraph.axons == self.metagraph.axons:
            return

        bt.logging.info(
            "Metagraph updated, re-syncing hotkeys, dendrite pool and moving averages"
        )
        # Zero out all hotkeys that have been replaced.
        for uid, hotkey in enumerate(self.hotkeys):
            if hotkey != self.metagraph.hotkeys[uid]:
                self.scores[uid] = 0  # hotkey has been replaced

        # Check to see if the metagraph has changed size.
        # If so, we need to add new hotkeys and moving averages.
        if len(self.hotkeys) < len(self.metagraph.hotkeys):
            # Update the size of the moving average scores.
            new_moving_average = np.zeros((self.metagraph.n))
            min_len = min(len(self.hotkeys), len(self.scores))
            new_moving_average[:min_len] = self.scores[:min_len]
            self.scores = new_moving_average

        # Update the hotkeys.
        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)

    def update_scores(self, rewards: np.ndarray, uids: List[int]):
        """Performs exponential moving average on the scores based on the rewards received from the miners."""

        # Check if rewards contains NaN values.
        if np.isnan(rewards).any():
            bt.logging.warning(f"NaN values detected in rewards: {rewards}")
            # Replace any NaN values in rewards with 0.
            rewards = np.nan_to_num(rewards, nan=0)

        # Ensure rewards is a numpy array.
        rewards = np.asarray(rewards)

        # Check if `uids` is already a numpy array and copy it to avoid the warning.
        if isinstance(uids, np.ndarray):
            uids_array = uids.copy()
        else:
            uids_array = np.array(uids)

        # Handle edge case: If either rewards or uids_array is empty.
        if rewards.size == 0 or uids_array.size == 0:
            bt.logging.info(f"rewards: {rewards}, uids_array: {uids_array}")
            bt.logging.warning(
                "Either rewards or uids_array is empty. No updates will be performed."
            )
            return

        # Check if sizes of rewards and uids_array match.
        if rewards.size != uids_array.size:
            raise ValueError(
                f"Shape mismatch: rewards array of shape {rewards.shape} "
                f"cannot be broadcast to uids array of shape {uids_array.shape}"
            )

        # Compute forward pass rewards, assumes uids are mutually exclusive.
        # shape: [ metagraph.n ]
        scattered_rewards: np.ndarray = np.zeros_like(self.scores)
        scattered_rewards[uids_array] = rewards
        bt.logging.debug(f"Scattered rewards: {rewards}")

        # Update scores with rewards produced by this step.
        # shape: [ metagraph.n ]
        alpha: float = self.config.neuron.moving_average_alpha
        self.scores: np.ndarray = (
            alpha * scattered_rewards + (1 - alpha) * self.scores
        )
        bt.logging.debug(f"Updated moving avg scores: {self.scores}")

    def save_state(self):
        """Saves the state of the validator to a file."""
        bt.logging.info("Saving validator state.")

        # Save the state of the validator to file.
        np.savez(
            self.config.neuron.full_path + "/state.npz",
            step=self.step,
            scores=self.scores,
            hotkeys=self.hotkeys,
            spec_version=self.spec_version,
        )

    def load_state(self):
        """Loads the state of the validator from a file."""
        bt.logging.info("Loading validator state.")

        state = np.load(self.config.neuron.full_path + "/state.npz", allow_pickle=False)
        loaded_scores = np.asarray(state["scores"], dtype=np.float32)
        loaded_hotkeys = [str(h) for h in state["hotkeys"].tolist()]
        current_hotkeys = list(self.metagraph.hotkeys)
        saved_spec = int(state["spec_version"]) if "spec_version" in state.files else None
        compatible = saved_spec == self.spec_version

        scores = np.zeros(self.metagraph.n, dtype=np.float32)
        if compatible:
            count = min(len(scores), len(loaded_scores), len(loaded_hotkeys))
            scores[:count] = loaded_scores[:count]
            for uid in range(count):
                if loaded_hotkeys[uid] != current_hotkeys[uid]:
                    scores[uid] = 0.0
        else:
            bt.logging.warning(
                f"Discarding score checkpoint from spec {saved_spec}; current spec is {self.spec_version}"
            )

        self.step = int(state["step"]) if compatible else 0
        self.scores = scores
        self.hotkeys = current_hotkeys
