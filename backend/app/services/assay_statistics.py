"""Reproducible statistics for experimental assay observations.

The module fits a four-parameter logistic curve to biological-replicate
summaries and estimates uncertainty by resampling complete biological
replicate profiles. Technical replicates are not treated as independent
biological observations.
"""
from __future__ import annotations

from itertools import permutations
from math import ceil, factorial, isfinite
from typing import Any, Iterable

import numpy as np
import scipy
from scipy.optimize import least_squares
from scipy.stats import rankdata

ALGORITHM_VERSION = "N3XORA_4PL_CLUSTER_BOOTSTRAP_V1"
COMPARISON_VERSION = "N3XORA_VINA_IC50_SPEARMAN_V1"
MIN_CONCENTRATIONS = 5
MIN_BIOLOGICAL_REPLICATES = 3
MIN_BOOTSTRAP_SUCCESS_RATE = 0.80


class AssayStatisticsError(ValueError):
    """A data or identifiability guard prevents a defensible analysis."""


def _four_parameter_logistic(params: np.ndarray, log_x: np.ndarray, direction: str) -> np.ndarray:
    bottom, top, log_midpoint, hill_slope = (float(x) for x in params)
    if direction == "increasing":
        exponent = (log_midpoint - log_x) * hill_slope
    elif direction == "decreasing":
        exponent = (log_x - log_midpoint) * hill_slope
    else:
        raise AssayStatisticsError("Le sens de réponse doit être increasing ou decreasing.")
    exponent = np.clip(exponent, -250.0, 250.0)
    fraction = 1.0 / (1.0 + np.power(10.0, exponent))
    return bottom + (top - bottom) * fraction


def _fit_curve(
    log_x: np.ndarray,
    y: np.ndarray,
    direction: str,
    initial: np.ndarray | None = None,
):
    y_min = float(np.min(y))
    y_max = float(np.max(y))
    y_span = y_max - y_min
    if not isfinite(y_span) or y_span <= max(1e-12, abs(y_max) * 1e-10):
        raise AssayStatisticsError("La réponse mesurée ne présente pas de plage dynamique suffisante.")

    x_min = float(np.min(log_x))
    x_max = float(np.max(log_x))
    if x_min == x_max:
        raise AssayStatisticsError("Au moins cinq concentrations positives distinctes sont nécessaires.")

    p0 = initial if initial is not None else np.array(
        [y_min, y_max, float(np.median(log_x)), 1.0], dtype=float
    )
    lower = np.array(
        [y_min - 5.0 * y_span, y_min + 0.51 * y_span, x_min - 2.0, 0.05],
        dtype=float,
    )
    upper = np.array(
        [y_min + 0.49 * y_span, y_max + 5.0 * y_span, x_max + 2.0, 5.0],
        dtype=float,
    )
    # The parameter bounds guarantee top > bottom and a positive Hill slope.
    p0 = np.minimum(np.maximum(p0, lower + 1e-8), upper - 1e-8)

    fit = least_squares(
        lambda params: _four_parameter_logistic(params, log_x, direction) - y,
        p0,
        bounds=(lower, upper),
        method="trf",
        loss="linear",
        x_scale="jac",
        max_nfev=5000,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
    )
    if not fit.success or not np.all(np.isfinite(fit.x)):
        raise AssayStatisticsError("L'ajustement 4PL n'a pas convergé de façon exploitable.")
    if fit.x[1] <= fit.x[0]:
        raise AssayStatisticsError("Les asymptotes 4PL ne sont pas ordonnées.")
    return fit


def prepare_biological_profiles(
    measurements: Iterable[Any],
    *,
    response_name: str,
    response_unit: str,
    concentration_unit: str,
    sample_label: str | None = None,
    timepoint_value: float | None = None,
    timepoint_unit: str | None = None,
) -> dict:
    """Select exact, unit-matched observations and collapse technical wells.

    Biological measurements define independent sample IDs. Technical wells are
    included only when their biological_sample_id explicitly points to one of
    those biological IDs at the same concentration/readout/time. Independent
    runs are not silently treated as biological replicates.
    """
    rows = list(measurements)
    matching = [
        row for row in rows
        if row.sample_role == "test_sample"
        and row.response_name == response_name
        and row.response_unit == response_unit
        and row.concentration_unit == concentration_unit
    ]
    if not matching:
        raise AssayStatisticsError(
            "Aucune mesure test_sample ne correspond exactement au readout et aux unités sélectionnés."
        )

    sample_labels = sorted({str(row.sample_label) for row in matching})
    if sample_label is None:
        if len(sample_labels) != 1:
            raise AssayStatisticsError(
                "Plusieurs échantillons sont présents. Sélectionner explicitement sample_label pour ne pas mélanger les composés."
            )
        sample_label = sample_labels[0]
    matching = [row for row in matching if str(row.sample_label) == sample_label]
    if not matching:
        raise AssayStatisticsError("Aucune mesure ne correspond au sample_label sélectionné.")

    timed = [
        row for row in matching
        if row.timepoint_value is not None or row.timepoint_unit is not None
    ]
    if timed and (timepoint_value is None or not timepoint_unit):
        raise AssayStatisticsError(
            "Les mesures sont horodatées: préciser explicitement le temps et son unité; ne mélangez pas plusieurs temps."
        )
    if (timepoint_value is None) != (timepoint_unit is None):
        raise AssayStatisticsError("Le temps et son unité doivent être fournis ensemble.")

    if timepoint_value is not None:
        matching = [
            row for row in matching
            if row.timepoint_value is not None
            and row.timepoint_unit == timepoint_unit
            and float(row.timepoint_value) == float(timepoint_value)
        ]
        if not matching:
            raise AssayStatisticsError("Aucune mesure ne correspond au temps sélectionné.")

    censored = [row for row in matching if row.qualifier != "exact"]
    if censored:
        raise AssayStatisticsError(
            "Les valeurs censurées (<, >, ≤, ≥) ne sont pas encore modélisées; "
            "elles ne peuvent pas être silencieusement traitées comme des valeurs exactes."
        )

    biological: dict[tuple[float, str], list[float]] = {}
    technical: dict[tuple[float, str], list[float]] = {}
    all_concentrations: set[float] = set()
    independent_run_rows = 0
    selected_biological_ids: set[str] = set()

    for row in matching:
        concentration = float(row.concentration_value)
        response = float(row.response_value)
        if not isfinite(concentration) or concentration <= 0:
            raise AssayStatisticsError("Les concentrations de dose-réponse doivent être strictement positives et finies.")
        if not isfinite(response):
            raise AssayStatisticsError("Les réponses doivent être des valeurs numériques finies.")

        if row.replicate_kind == "biological":
            replicate_id = str(row.replicate_id).strip()
            if not replicate_id:
                raise AssayStatisticsError("Un identifiant de réplicat biologique est obligatoire.")
            biological.setdefault((concentration, replicate_id), []).append(response)
            selected_biological_ids.add(replicate_id)
            all_concentrations.add(concentration)
        elif row.replicate_kind == "technical":
            parent_id = str(getattr(row, "biological_sample_id", None) or "").strip()
            if not parent_id:
                raise AssayStatisticsError(
                    "Chaque réplicat technique doit référencer biological_sample_id; "
                    "Nexora ne déduit pas le parent biologique à partir du nom du puits."
                )
            technical.setdefault((concentration, parent_id), []).append(response)
        else:
            independent_run_rows += 1

    biological_keys = set(biological)
    invalid_technical = [
        (concentration, parent_id)
        for concentration, parent_id in technical
        if (concentration, parent_id) not in biological_keys
    ]
    if invalid_technical:
        raise AssayStatisticsError(
            "Un ou plusieurs réplicats techniques ne pointent pas vers un réplicat biologique "
            "à la même concentration; vérifier biological_sample_id et les unités."
        )

    if len(all_concentrations) < MIN_CONCENTRATIONS:
        raise AssayStatisticsError(
            f"Au moins {MIN_CONCENTRATIONS} concentrations biologiquement observées et distinctes sont requises pour l'ajustement 4PL."
        )

    combined: dict[str, dict[float, list[float]]] = {}
    for (concentration, replicate_id), values in biological.items():
        combined.setdefault(replicate_id, {}).setdefault(concentration, []).extend(values)
    for (concentration, replicate_id), values in technical.items():
        combined.setdefault(replicate_id, {}).setdefault(concentration, []).extend(values)

    concentrations = sorted(all_concentrations)
    complete_ids = sorted(
        replicate_id for replicate_id, profile in combined.items()
        if all(concentration in profile for concentration in concentrations)
    )
    if len(complete_ids) < MIN_BIOLOGICAL_REPLICATES:
        raise AssayStatisticsError(
            f"Au moins {MIN_BIOLOGICAL_REPLICATES} réplicats biologiques distincts, "
            "chacun mesuré à toutes les concentrations sélectionnées, sont requis. "
            "Les réplicats indépendants non biologiques ne remplacent pas les réplicats biologiques."
        )

    paired = {
        replicate_id: {
            concentration: float(np.mean(combined[replicate_id][concentration]))
            for concentration in concentrations
        }
        for replicate_id in complete_ids
    }
    log_x = np.log10(np.asarray(concentrations, dtype=float))
    x_values: list[float] = []
    y_values: list[float] = []
    dose_summary = []
    for concentration, lx in zip(concentrations, log_x):
        values = np.asarray([paired[replicate_id][concentration] for replicate_id in complete_ids], dtype=float)
        x_values.extend([float(lx)] * len(values))
        y_values.extend(values.tolist())
        dose_summary.append({
            "concentration": float(concentration),
            "concentration_unit": concentration_unit,
            "mean_response": float(np.mean(values)),
            "sd_biological": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "n_biological_replicates": int(len(values)),
        })

    selected_ids = {
        int(row.id) for row in matching
        if (
            row.replicate_kind == "biological" and str(row.replicate_id) in complete_ids
        ) or (
            row.replicate_kind == "technical"
            and str(row.biological_sample_id) in complete_ids
        )
    }
    selected_rows = [row for row in matching if row.id in selected_ids]
    technical_used = sum(1 for row in selected_rows if row.replicate_kind == "technical")
    return {
        "profiles": paired,
        "concentrations": concentrations,
        "log_x": np.asarray(x_values, dtype=float),
        "responses": np.asarray(y_values, dtype=float),
        "dose_summary": dose_summary,
        "biological_replicate_ids": complete_ids,
        "selected_measurement_ids": sorted(selected_ids),
        "sample_label": sample_label,
        "n_biological_replicates": len(complete_ids),
        "n_technical_measurements_used": int(technical_used),
        "n_independent_run_rows_not_used": int(independent_run_rows),
        "n_selected_rows": len(selected_rows),
        "timepoint_value": timepoint_value,
        "timepoint_unit": timepoint_unit,
    }

def fit_dose_response_4pl(
    measurements: Iterable[Any],
    *,
    response_name: str,
    response_unit: str,
    concentration_unit: str,
    response_direction: str,
    endpoint_name: str,
    timepoint_value: float | None = None,
    timepoint_unit: str | None = None,
    sample_label: str | None = None,
    bootstrap_iterations: int = 1000,
    random_seed: int = 42,
) -> dict:
    if response_direction not in {"increasing", "decreasing"}:
        raise AssayStatisticsError("response_direction doit être increasing ou decreasing.")
    if endpoint_name not in {"IC50", "EC50"}:
        raise AssayStatisticsError("L'endpoint dérivé doit être explicitement étiqueté IC50 ou EC50.")
    if bootstrap_iterations < 500 or bootstrap_iterations > 2000:
        raise AssayStatisticsError("Le bootstrap doit utiliser entre 500 et 2 000 itérations.")
    if random_seed < 0 or random_seed > 2_147_483_647:
        raise AssayStatisticsError("random_seed doit être compris entre 0 et 2 147 483 647.")

    prepared = prepare_biological_profiles(
        measurements,
        response_name=response_name,
        response_unit=response_unit,
        concentration_unit=concentration_unit,
        sample_label=sample_label,
        timepoint_value=timepoint_value,
        timepoint_unit=timepoint_unit,
    )
    log_x = prepared["log_x"]
    y = prepared["responses"]
    point_fit = _fit_curve(log_x, y, response_direction)
    params = point_fit.x
    predicted = _four_parameter_logistic(params, log_x, response_direction)
    residuals = y - predicted
    ss_res = float(np.sum(np.square(residuals)))
    ss_total = float(np.sum(np.square(y - np.mean(y))))
    r_squared = 1.0 - ss_res / ss_total if ss_total > 0 else None
    rmse = float(np.sqrt(np.mean(np.square(residuals))))

    rng = np.random.default_rng(random_seed)
    replicate_ids = prepared["biological_replicate_ids"]
    profiles = prepared["profiles"]
    bootstrap_midpoints: list[float] = []
    concentrations = prepared["concentrations"]
    dose_log = np.log10(np.asarray(concentrations, dtype=float))
    for _ in range(bootstrap_iterations):
        sampled_ids = rng.choice(replicate_ids, size=len(replicate_ids), replace=True)
        by_cluster = [
            [profiles[str(replicate_id)][concentration] for concentration in concentrations]
            for replicate_id in sampled_ids
        ]
        boot_y = np.asarray(by_cluster, dtype=float).reshape(-1)
        boot_x = np.tile(dose_log, len(sampled_ids))
        try:
            boot_fit = _fit_curve(boot_x, boot_y, response_direction, initial=params)
            midpoint = float(np.power(10.0, boot_fit.x[2]))
            if isfinite(midpoint) and midpoint > 0:
                bootstrap_midpoints.append(midpoint)
        except (AssayStatisticsError, ValueError, FloatingPointError):
            continue

    success_rate = len(bootstrap_midpoints) / float(bootstrap_iterations)
    if success_rate < MIN_BOOTSTRAP_SUCCESS_RATE:
        raise AssayStatisticsError(
            "L'incertitude de l'estimation est instable: moins de 80 % des réplications bootstrap ont convergé."
        )

    ci_low, ci_high = np.quantile(np.asarray(bootstrap_midpoints), [0.025, 0.975])
    bottom, top, log_midpoint, hill_slope = (float(x) for x in params)
    midpoint = float(np.power(10.0, log_midpoint))
    min_concentration = float(min(concentrations))
    max_concentration = float(max(concentrations))
    midpoint_interpolated = min_concentration <= midpoint <= max_concentration
    amplitude = max(top - bottom, 1e-12)
    observed_by_dose = {
        item["concentration"]: item["mean_response"]
        for item in prepared["dose_summary"]
    }
    low_observed = observed_by_dose[min_concentration]
    high_observed = observed_by_dose[max_concentration]
    if response_direction == "increasing":
        lower_plateau_distance = abs(low_observed - bottom) / amplitude
        upper_plateau_distance = abs(high_observed - top) / amplitude
    else:
        lower_plateau_distance = abs(low_observed - top) / amplitude
        upper_plateau_distance = abs(high_observed - bottom) / amplitude
    lower_plateau_covered = lower_plateau_distance <= 0.20
    upper_plateau_covered = upper_plateau_distance <= 0.20

    warnings = []
    if not midpoint_interpolated:
        warnings.append("Le midpoint 4PL est hors de l'intervalle de concentrations testé: estimation extrapolée.")
    if not lower_plateau_covered:
        warnings.append("Le plateau de réponse à faible concentration est insuffisamment observé.")
    if not upper_plateau_covered:
        warnings.append("Le plateau de réponse à forte concentration est insuffisamment observé.")
    if r_squared is not None and r_squared < 0.80:
        warnings.append("R² inférieur à 0,80: examiner les résidus et l'adéquation du modèle.")
    if hill_slope <= 0.051 or hill_slope >= 4.99:
        warnings.append("La pente de Hill est proche d'une borne d'ajustement; l'estimation peut être insuffisamment identifiée.")
    quality_status = (
        "interpolated"
        if midpoint_interpolated and lower_plateau_covered and upper_plateau_covered
        and (r_squared is None or r_squared >= 0.80)
        and hill_slope > 0.051 and hill_slope < 4.99
        else "requires_review"
    )

    curve_log_x = np.linspace(float(np.min(log_x)), float(np.max(log_x)), 80)
    curve_x = np.power(10.0, curve_log_x)
    curve_y = _four_parameter_logistic(params, curve_log_x, response_direction)
    fitted_at_doses = _four_parameter_logistic(params, dose_log, response_direction)

    return {
        "analysis_type": "dose_response_4pl",
        "algorithm_version": ALGORITHM_VERSION,
        "libraries": {"numpy": np.__version__, "scipy": scipy.__version__},
        "model": {
            "name": "four_parameter_logistic",
            "equation": "bottom + (top-bottom)/(1 + 10^exponent); exponent sign follows declared response direction",
            "response_direction": response_direction,
            "endpoint_label": endpoint_name,
            "endpoint_semantics": "The reported concentration is the relative midpoint halfway between fitted top and bottom. It is not necessarily the absolute concentration giving exactly 50% raw response when fitted asymptotes differ from 0 and 100.",
        },
        "fit": {
            "bottom": bottom,
            "top": top,
            "hill_slope": hill_slope,
            "log10_midpoint": log_midpoint,
            "midpoint_concentration": midpoint,
            "concentration_unit": concentration_unit,
            "r_squared": float(r_squared) if r_squared is not None else None,
            "rmse": rmse,
            "residual_sum_squares": ss_res,
            "degrees_of_freedom": max(0, int(len(y) - 4)),
            "optimizer_status": int(point_fit.status),
            "optimizer_message": str(point_fit.message),
        },
        "confidence_interval": {
            "level": 0.95,
            "method": "biological_replicate_cluster_bootstrap_percentile",
            "lower": float(ci_low),
            "upper": float(ci_high),
            "unit": concentration_unit,
            "iterations_requested": int(bootstrap_iterations),
            "iterations_converged": int(len(bootstrap_midpoints)),
            "convergence_rate": float(success_rate),
            "random_seed": int(random_seed),
        },
        "quality": {
            "status": quality_status,
            "midpoint_within_tested_range": bool(midpoint_interpolated),
            "lower_plateau_covered": bool(lower_plateau_covered),
            "upper_plateau_covered": bool(upper_plateau_covered),
            "lower_plateau_relative_distance": float(lower_plateau_distance),
            "upper_plateau_relative_distance": float(upper_plateau_distance),
            "warnings": warnings,
            "note": "Le seuil R²=0,80 est un indicateur opérationnel de revue, pas un critère universel de validité pharmacologique.",
        },
        "sample": {
            "response_name": response_name,
            "response_unit": response_unit,
            "sample_label": prepared["sample_label"],
            "concentration_unit": concentration_unit,
            "timepoint_value": prepared["timepoint_value"],
            "timepoint_unit": prepared["timepoint_unit"],
            "n_biological_replicates": int(prepared["n_biological_replicates"]),
            "n_biological_summary_observations": int(len(y)),
            "n_selected_measurement_rows": int(prepared["n_selected_rows"]),
            "n_technical_measurements_used": int(prepared["n_technical_measurements_used"]),
            "n_independent_run_rows_not_used": int(prepared["n_independent_run_rows_not_used"]),
            "selected_measurement_ids": prepared["selected_measurement_ids"],
            "n_concentrations": int(len(concentrations)),
            "concentration_min": min_concentration,
            "concentration_max": max_concentration,
        },
        "dose_summary": [
            {**item, "fitted_response": float(fitted)}
            for item, fitted in zip(prepared["dose_summary"], fitted_at_doses)
        ],
        "fitted_curve": [
            {"concentration": float(x), "fitted_response": float(y_value)}
            for x, y_value in zip(curve_x, curve_y)
        ],
        "scientific_notice": (
            "Ajustement exploratoire des mesures expérimentales déclarées. "
            "Le bootstrap rééchantillonne les profils biologiques complets; "
            "les réplicats techniques ne gonflent pas le n biologique. "
            "Le résultat ne constitue ni une réplication indépendante ni une validation clinique."
        ),
    }


def spearman_rank_comparison(
    vina_scores: Iterable[float],
    log10_midpoints: Iterable[float],
    *,
    random_seed: int = 42,
    monte_carlo_resamples: int = 10000,
) -> dict:
    """Compare ranks of Vina score and log10 experimental midpoint.

    Lower Vina score and lower midpoint both rank as more favorable/more potent,
    so concordance is a positive Spearman rho. This is a descriptive comparison,
    not calibration of Vina to an affinity/free-energy scale.
    """
    x = np.asarray(list(vina_scores), dtype=float)
    y = np.asarray(list(log10_midpoints), dtype=float)
    if x.size != y.size or x.size < 5:
        raise AssayStatisticsError("Au moins cinq paires composé–mesure sont requises pour la comparaison de rangs.")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise AssayStatisticsError("Les scores et midpoint doivent être numériques et finis.")
    rank_x = rankdata(x, method="average")
    rank_y = rankdata(y, method="average")
    if np.ptp(rank_x) == 0 or np.ptp(rank_y) == 0:
        raise AssayStatisticsError("Une corrélation de rang est indéfinie si l'un des deux classements est constant.")
    rho = float(np.corrcoef(rank_x, rank_y)[0, 1])
    observed = abs(rho)
    n = int(x.size)

    bootstrap_rng = np.random.default_rng(random_seed + 1)
    bootstrap_rhos: list[float] = []
    for _ in range(2000):
        sample_indices = bootstrap_rng.integers(0, n, size=n)
        sample_x = x[sample_indices]
        sample_y = y[sample_indices]
        sample_rank_x = rankdata(sample_x, method="average")
        sample_rank_y = rankdata(sample_y, method="average")
        if np.ptp(sample_rank_x) == 0 or np.ptp(sample_rank_y) == 0:
            continue
        bootstrap_rhos.append(float(np.corrcoef(sample_rank_x, sample_rank_y)[0, 1]))
    bootstrap_ci = (
        [float(v) for v in np.quantile(bootstrap_rhos, [0.025, 0.975])]
        if len(bootstrap_rhos) >= 1000 else None
    )

    if n <= 8:
        extreme = 0
        total = 0
        for order in permutations(range(n)):
            permuted = rank_y[list(order)]
            statistic = float(np.corrcoef(rank_x, permuted)[0, 1])
            if abs(statistic) >= observed - 1e-12:
                extreme += 1
            total += 1
        p_value = extreme / total
        permutation_method = "exact_all_pairings"
        resamples = total
    else:
        rng = np.random.default_rng(random_seed)
        extreme = 0
        for _ in range(monte_carlo_resamples):
            permuted = rng.permutation(rank_y)
            statistic = float(np.corrcoef(rank_x, permuted)[0, 1])
            if abs(statistic) >= observed - 1e-12:
                extreme += 1
        p_value = (extreme + 1) / (monte_carlo_resamples + 1)
        permutation_method = "monte_carlo_two_sided"
        resamples = int(monte_carlo_resamples)

    return {
        "method": "spearman_rank_correlation",
        "rho": rho,
        "permutation_p_value_two_sided": float(p_value),
        "permutation_method": permutation_method,
        "permutation_resamples": int(resamples),
        "bootstrap_ci_95": bootstrap_ci,
        "bootstrap_resamples_requested": 2000,
        "bootstrap_resamples_valid": int(len(bootstrap_rhos)),
        "random_seed": int(random_seed),
        "n_compounds": n,
        "expected_concordance": "positive rho: lower Vina scores tend to accompany lower fitted midpoint concentrations",
        "scientific_notice": (
            "Comparaison descriptive dans un groupe expérimental déclaré comparable. "
            "Un score Vina n'est ni une mesure d'IC50, ni un ΔG expérimental, ni un Kd; "
            "la corrélation ne valide pas la capacité prédictive générale du docking."
        ),
    }
