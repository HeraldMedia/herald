"""
Patch for routing Herald's logging (herald.sdk.log) to standard Python logging.

IMPORTANT: This module must be imported before any other modules that log through herald.
Example usage:
  
  # At the very top of your entry point script:
  import bt_logging_patch
  
  # Then continue with your normal imports
  import other_modules
"""

import logging

# Configure logging to show all logs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

print("🔧 Patching herald logging with standard logging")

from herald.sdk.log import log  # noqa: E402

# Herald and the bittensor SDK now log through the root handler configured above, at DEBUG.
log.route_to_root()
log.setLevel(logging.DEBUG)

print("✅ Patching complete - herald.sdk.log now uses standard logging")
