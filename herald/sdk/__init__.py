"""Herald's thin layer over the bittensor SDK.

Every use of bittensor in Herald goes through this package:

- ``herald.sdk.log``: logging with the method names Herald has always called (``info``,
  ``warning``, ``debug``, ``error``, ``success``, ``trace``) and the ``--logging.*`` flags, on the
  standard library.
- ``herald.sdk.config``: the command-line configuration (``--wallet.*``, ``--subtensor.*``,
  ``--logging.*``, ``--netuid``, ``--neuron.*``) as a nested attribute tree.
- ``herald.sdk.chain``: the chain reads and writes Herald performs, with fixed return types and
  units.
- ``herald.sdk.metagraph``: the per-UID view of a subnet that the validator keeps.
- ``herald.sdk.keys``: wallets and sr25519 keypairs.

Modules are imported on demand so that ``herald.sdk.log`` stays importable without the SDK.
"""
