"""Funding → boost: a client's earmarked α holding sizes a brief's boost (Bitcast-aligned).

Two pure functions, both unit-testable without the validator runtime:

  * ``clamp_boost`` — the CONSENSUS RAIL every validator enforces in scoring. A brief's effective
    boost is bounded to [1, boost_max], so a signed (or compromised) brief registry can never exceed
    the protocol max.
  * ``boost_for_alpha`` — the OPERATOR-SIDE pricing curve (advisory): earmarked α -> boost, sqrt with
    diminishing returns, capped at boost_max. The operator/console uses it to price funding; the clamp
    above is the binding limit. (Tier B would move this derivation on-chain.)

See FUNDING_DESIGN.md.
"""

import math


def clamp_boost(boost, boost_max: float) -> float:
    """Bound a brief's boost to [1.0, boost_max]; non-numeric / NaN -> 1.0."""
    try:
        b = float(boost)
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(b):
        return 1.0
    return max(1.0, min(b, float(boost_max)))


def boost_for_alpha(alpha: float, alpha_for_max: float, boost_max: float) -> float:
    """Earmarked α -> boost. 0 α -> 1.0x; alpha_for_max -> boost_max; sqrt (front-loaded) between."""
    if alpha <= 0 or alpha_for_max <= 0:
        return 1.0
    frac = min(1.0, math.sqrt(alpha / alpha_for_max))
    return 1.0 + (boost_max - 1.0) * frac
