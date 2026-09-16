"""Numerically stable Boltzmann-like aggregations for empirical docking scores.

The inputs are empirical docking scores (e.g. AutoDock Vina scores), not
thermodynamic free energies.  The Boltzmann transform is therefore used here
as a *score aggregation heuristic*.  In particular, the temperature is an
aggregation scale and must not be interpreted as proving a physical binding
free energy.
"""

import math
from typing import List

R_KCAL_MOL_K = 0.00198720425864083


def _validate(scores: List[float], temperature: float) -> None:
    if not scores:
        raise ValueError("At least one docking score is required")
    if temperature <= 0:
        raise ValueError("Temperature must be positive")
    if not all(math.isfinite(float(score)) for score in scores):
        raise ValueError("All docking scores must be finite")


def _log_weights(scores: List[float], temperature: float) -> List[float]:
    _validate(scores, temperature)
    beta = 1.0 / (R_KCAL_MOL_K * temperature)
    return [-beta * float(score) for score in scores]


def _logsumexp(values: List[float]) -> float:
    maximum = max(values)
    return maximum + math.log(sum(math.exp(value - maximum) for value in values))


def boltzmann_weights(scores: List[float], temperature: float = 298.15) -> List[float]:
    """Return normalized Boltzmann-like weights for empirical docking scores."""
    log_weights = _log_weights(scores, temperature)
    normalizer = _logsumexp(log_weights)
    return [math.exp(value - normalizer) for value in log_weights]


def logsumexp_score(scores: List[float], temperature: float = 298.15) -> float:
    """Return the unnormalized Boltzmann log-sum score.

    ``-RT log(sum(exp(-score/RT)))`` is intentionally retained as a diagnostic
    quantity.  Because it depends on the number of ensemble members, it should
    not be used as the canonical score when ensembles have different sizes.
    """
    log_weights = _log_weights(scores, temperature)
    return -(R_KCAL_MOL_K * temperature) * _logsumexp(log_weights)


def boltzmann_logmean_score(scores: List[float], temperature: float = 298.15) -> float:
    """Return a size-normalized Boltzmann aggregate for one pose ensemble.

    The arithmetic mean is taken in Boltzmann space before applying ``-RT ln``.
    This prevents a pocket from becoming artificially more favorable merely
    because it contains more independent pose clusters.  It is still an
    empirical score aggregate, not a binding free energy.
    """
    log_weights = _log_weights(scores, temperature)
    log_mean = _logsumexp(log_weights) - math.log(len(scores))
    return -(R_KCAL_MOL_K * temperature) * log_mean


def boltzmann_mean(scores: List[float], temperature: float = 298.15) -> float:
    """Return the weighted mean of empirical docking scores."""
    weights = boltzmann_weights(scores, temperature)
    return sum(weight * float(score) for weight, score in zip(weights, scores))
