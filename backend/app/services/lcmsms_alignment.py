"""Deterministic, polarity-aware inter-injection LC-MS feature alignment.

This first slice deliberately uses transparent Python transformations rather
than hiding alignment in an opaque preprocessing step. Retention-time maps are
piecewise-linear and never extrapolated outside observed anchor coverage.
"""
from __future__ import annotations

import bisect
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


ALIGNMENT_PROTOCOL_VERSION = "1.0.0"


class LcmsAlignmentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FeatureInput:
    feature_id: int
    processing_run_id: int
    injection_id: int
    injection_order: int
    feature_key: str
    precursor_mz: float
    retention_time_seconds: float
    polarity: str
    charge: int | None
    adduct: str | None
    raw_intensity: float | None
    source_observation_id: int | None = None


@dataclass(frozen=True)
class InjectionInput:
    injection_id: int
    injection_order: int
    injection_type: str


@dataclass(frozen=True)
class AlignmentOutput:
    groups: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LcmsAlignmentError("INVALID_ALIGNMENT_INPUT", f"{label} doit être numérique.") from exc
    if not math.isfinite(number):
        raise LcmsAlignmentError("INVALID_ALIGNMENT_INPUT", f"{label} doit être fini.")
    return number


def _positive(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise LcmsAlignmentError("INVALID_ALIGNMENT_PARAMETER", f"{label} doit être strictement positif.")
    return number


def mass_error_ppm(observed: float, reference: float) -> float:
    if not math.isfinite(observed) or not math.isfinite(reference) or reference <= 0:
        raise ValueError("m/z values must be finite and reference m/z must be positive")
    return abs(observed - reference) / reference * 1_000_000.0


def _compatible(left: FeatureInput, right: FeatureInput) -> bool:
    if left.polarity != right.polarity:
        return False
    if left.charge is not None and right.charge is not None and left.charge != right.charge:
        return False
    if left.adduct and right.adduct and left.adduct.strip() != right.adduct.strip():
        return False
    return True


def _unique_anchor_pairs(
    source: Sequence[FeatureInput],
    reference: Sequence[FeatureInput],
    *,
    mass_tolerance_ppm: float,
    max_initial_rt_delta_seconds: float,
) -> list[dict[str, float]]:
    """Retain only mutual one-to-one m/z/RT anchor candidates."""
    sorted_reference = sorted(reference, key=lambda item: item.precursor_mz)
    reference_mz = [item.precursor_mz for item in sorted_reference]
    source_matches: dict[int, list[tuple[int, float, float]]] = {}
    reference_candidate_counts: Counter[int] = Counter()

    for source_feature in source:
        width = source_feature.precursor_mz * mass_tolerance_ppm / 1_000_000.0
        lo = bisect.bisect_left(reference_mz, source_feature.precursor_mz - width)
        hi = bisect.bisect_right(reference_mz, source_feature.precursor_mz + width)
        candidates: list[tuple[int, float, float]] = []
        for sorted_index in range(lo, hi):
            reference_feature = sorted_reference[sorted_index]
            if not _compatible(source_feature, reference_feature):
                continue
            ppm = mass_error_ppm(source_feature.precursor_mz, reference_feature.precursor_mz)
            rt_delta = abs(
                source_feature.retention_time_seconds - reference_feature.retention_time_seconds
            )
            if ppm <= mass_tolerance_ppm and rt_delta <= max_initial_rt_delta_seconds:
                candidates.append((reference_feature.feature_id, ppm, rt_delta))
        if len(candidates) == 1:
            source_matches[source_feature.feature_id] = candidates
            reference_candidate_counts[candidates[0][0]] += 1

    anchors = []
    reference_by_id = {item.feature_id: item for item in reference}
    source_by_id = {item.feature_id: item for item in source}
    for source_id, candidates in source_matches.items():
        reference_id, ppm, rt_delta = candidates[0]
        if reference_candidate_counts[reference_id] != 1:
            continue
        source_feature = source_by_id[source_id]
        reference_feature = reference_by_id[reference_id]
        anchors.append({
            "source_feature_id": float(source_id),
            "reference_feature_id": float(reference_id),
            "source_rt": source_feature.retention_time_seconds,
            "reference_rt": reference_feature.retention_time_seconds,
            "mass_error_ppm": ppm,
            "initial_rt_delta_seconds": rt_delta,
        })
    anchors.sort(key=lambda item: (item["source_rt"], item["source_feature_id"]))
    return anchors


def _theil_sen_line(anchors: Sequence[Mapping[str, float]]) -> tuple[float, float]:
    """Robust positive-slope summary line, with bounded deterministic sampling."""
    points = [(float(a["source_rt"]), float(a["reference_rt"])) for a in anchors]
    # Adjacent slopes after RT ordering are O(n), deterministic and sufficiently
    # robust for rejecting gross anchor mismatches before piecewise interpolation.
    slopes = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx = x2 - x1
        if dx > 1e-9:
            slope = (y2 - y1) / dx
            if math.isfinite(slope) and slope > 0:
                slopes.append(slope)
    if not slopes:
        raise LcmsAlignmentError(
            "ALIGNMENT_NON_MONOTONIC_ANCHORS",
            "Les repères ne définissent pas une pente RT strictement positive.",
        )
    slope = statistics.median(slopes)
    intercept = statistics.median(y - slope * x for x, y in points)
    return slope, intercept


def _prepare_monotonic_knots(
    anchors: Sequence[Mapping[str, float]],
    *,
    max_anchor_residual_seconds: float,
    minimum_anchors: int,
) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    if len(anchors) < minimum_anchors:
        raise LcmsAlignmentError(
            "ALIGNMENT_INSUFFICIENT_ANCHORS",
            f"{len(anchors)} repères uniques ; {minimum_anchors} sont requis.",
        )
    slope, intercept = _theil_sen_line(anchors)
    residuals = [
        float(a["reference_rt"]) - (slope * float(a["source_rt"]) + intercept)
        for a in anchors
    ]
    center = statistics.median(residuals)
    abs_deviations = [abs(value - center) for value in residuals]
    mad = statistics.median(abs_deviations)
    robust_limit = max(
        max_anchor_residual_seconds,
        3.5 * 1.4826 * mad,
    )
    filtered = [
        anchor for anchor, residual in zip(anchors, residuals)
        if abs(residual - center) <= robust_limit
    ]
    if len(filtered) < minimum_anchors:
        raise LcmsAlignmentError(
            "ALIGNMENT_ANCHOR_OUTLIERS",
            f"Après filtrage robuste, {len(filtered)} repères restent ; {minimum_anchors} sont requis.",
        )

    # Collapse identical source RT values, then retain a strictly increasing
    # target sequence. Crossings are excluded, never sorted away into false order.
    by_source_rt: dict[float, list[float]] = defaultdict(list)
    for anchor in filtered:
        by_source_rt[float(anchor["source_rt"])].append(float(anchor["reference_rt"]))
    raw_knots = [
        (source_rt, statistics.median(target_values))
        for source_rt, target_values in sorted(by_source_rt.items())
    ]
    knots: list[tuple[float, float]] = []
    for source_rt, target_rt in raw_knots:
        if not knots or target_rt > knots[-1][1] + 1e-6:
            knots.append((source_rt, target_rt))
    if len(knots) < minimum_anchors:
        raise LcmsAlignmentError(
            "ALIGNMENT_NON_MONOTONIC_ANCHORS",
            f"Seulement {len(knots)} repères monotones restent après filtrage.",
        )
    return knots, {
        "raw_anchor_count": len(anchors),
        "robust_anchor_count": len(filtered),
        "monotonic_knot_count": len(knots),
        "robust_linear_slope": slope,
        "robust_linear_intercept_seconds": intercept,
        "median_absolute_residual_seconds": statistics.median(abs_deviations),
        "max_anchor_residual_threshold_seconds": robust_limit,
        "anchor_source_rt_min_seconds": knots[0][0],
        "anchor_source_rt_max_seconds": knots[-1][0],
        "anchor_reference_rt_min_seconds": knots[0][1],
        "anchor_reference_rt_max_seconds": knots[-1][1],
    }


def _piecewise_transform(
    retention_time_seconds: float,
    knots: Sequence[tuple[float, float]],
) -> float | None:
    """Transform RT only inside the anchor interval; never extrapolate."""
    x = float(retention_time_seconds)
    if x < knots[0][0] or x > knots[-1][0]:
        return None
    xs = [item[0] for item in knots]
    index = bisect.bisect_right(xs, x)
    if index == 0:
        return knots[0][1]
    if index >= len(knots):
        return knots[-1][1]
    x0, y0 = knots[index - 1]
    x1, y1 = knots[index]
    if x1 <= x0:
        return None
    ratio = (x - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def _select_reference_injections(
    features: Sequence[FeatureInput],
    injections: Sequence[InjectionInput],
    *,
    requested_references: Mapping[str, int] | None,
) -> tuple[dict[str, int], dict[str, str]]:
    by_injection_and_polarity: dict[tuple[int, str], int] = Counter()
    available_polarities = sorted({item.polarity for item in features if item.polarity in {"positive", "negative"}})
    injection_by_id = {item.injection_id: item for item in injections}
    for item in features:
        by_injection_and_polarity[(item.injection_id, item.polarity)] += 1

    references: dict[str, int] = {}
    methods: dict[str, str] = {}
    requested = dict(requested_references or {})
    for polarity in available_polarities:
        if polarity in requested:
            reference_id = int(requested[polarity])
            if by_injection_and_polarity.get((reference_id, polarity), 0) == 0:
                raise LcmsAlignmentError(
                    "ALIGNMENT_REFERENCE_INVALID",
                    f"L'injection de référence {reference_id} ne contient aucune feature {polarity}.",
                )
            if reference_id not in injection_by_id:
                raise LcmsAlignmentError("ALIGNMENT_REFERENCE_INVALID", "Injection de référence inconnue.")
            references[polarity] = reference_id
            methods[polarity] = "user_selected"
            continue

        candidates = [
            injection for injection in injections
            if by_injection_and_polarity.get((injection.injection_id, polarity), 0) > 0
        ]
        if not candidates:
            continue
        pooled = [
            injection for injection in candidates
            if injection.injection_type == "pooled_qc"
        ]
        pool = pooled or candidates
        selected = max(
            pool,
            key=lambda injection: (
                by_injection_and_polarity[(injection.injection_id, polarity)],
                -injection.injection_order,
                -injection.injection_id,
            ),
        )
        references[polarity] = selected.injection_id
        methods[polarity] = "pooled_qc_highest_feature_count" if pooled else "highest_feature_count_fallback"
    return references, methods


def _group_aligned_features(
    features: Sequence[FeatureInput],
    mapped: Mapping[int, float | None],
    *,
    expected_injection_counts: Mapping[str, int],
    group_mass_tolerance_ppm: float,
    group_rt_tolerance_seconds: float,
) -> list[dict[str, Any]]:
    # Features outside their injection's anchor interval are preserved as
    # singletons. Their original RT is retained, but they can never be merged.
    eligible = [
        item for item in features
        if item.polarity in {"positive", "negative"}
    ]
    eligible.sort(key=lambda item: (
        item.polarity,
        item.precursor_mz,
        float(mapped[item.feature_id] if mapped.get(item.feature_id) is not None else item.retention_time_seconds),
        item.injection_order,
        item.feature_id,
    ))
    clusters: list[dict[str, Any]] = []
    for feature in eligible:
        if mapped.get(feature.feature_id) is None:
            clusters.append({
                "polarity": feature.polarity,
                "members": [feature],
                "injection_ids": {feature.injection_id},
                "unaligned_singleton": True,
            })
            continue
        best: tuple[float, int] | None = None
        for cluster_index, cluster in enumerate(clusters):
            if cluster["polarity"] != feature.polarity or cluster.get("unaligned_singleton"):
                continue
            if feature.injection_id in cluster["injection_ids"]:
                continue
            members = cluster["members"]
            if any(not _compatible(feature, member) for member in members):
                continue
            consensus_mz = statistics.median(item.precursor_mz for item in members)
            consensus_rt = statistics.median(mapped[item.feature_id] for item in members)
            ppm = mass_error_ppm(feature.precursor_mz, consensus_mz)
            rt_delta = abs(float(mapped[feature.feature_id]) - consensus_rt)
            if ppm > group_mass_tolerance_ppm or rt_delta > group_rt_tolerance_seconds:
                continue
            distance = ppm / group_mass_tolerance_ppm + rt_delta / group_rt_tolerance_seconds
            if best is None or distance < best[0]:
                best = (distance, cluster_index)
        if best is None:
            clusters.append({
                "polarity": feature.polarity,
                "members": [feature],
                "injection_ids": {feature.injection_id},
            })
        else:
            cluster = clusters[best[1]]
            cluster["members"].append(feature)
            cluster["injection_ids"].add(feature.injection_id)

    results = []
    for cluster in clusters:
        members = cluster["members"]
        injection_count = len(cluster["injection_ids"])
        expected_count = int(expected_injection_counts.get(cluster["polarity"], injection_count))
        member_rts = [
            float(mapped[item.feature_id])
            if mapped.get(item.feature_id) is not None
            else item.retention_time_seconds
            for item in members
        ]
        unaligned_singleton = bool(cluster.get("unaligned_singleton"))
        results.append({
            "polarity": cluster["polarity"],
            "consensus_mz": statistics.median(item.precursor_mz for item in members),
            "aligned_retention_time": statistics.median(member_rts),
            "member_count": len(members),
            "injection_count": injection_count,
            "expected_injection_count": expected_count,
            "alignment_status": "singleton" if len(members) == 1 else (
                "aligned" if len(members) == expected_count else "partial"
            ),
            "members": [{
                "feature": item,
                "aligned_retention_time": (
                    float(mapped[item.feature_id])
                    if mapped.get(item.feature_id) is not None
                    else item.retention_time_seconds
                ),
                "delta_retention_time_seconds": (
                    float(mapped[item.feature_id]) - item.retention_time_seconds
                    if mapped.get(item.feature_id) is not None else 0.0
                ),
                "is_rt_aligned": mapped.get(item.feature_id) is not None,
                "delta_mz_ppm": (
                    item.precursor_mz - statistics.median(member.precursor_mz for member in members)
                ) / statistics.median(member.precursor_mz for member in members) * 1_000_000.0,
            } for item in sorted(members, key=lambda item: (item.injection_order, item.feature_id))],
        })
    results.sort(key=lambda item: (
        item["polarity"], item["consensus_mz"], item["aligned_retention_time"]
    ))
    return results


def align_lcms_features(
    features: Sequence[FeatureInput],
    injections: Sequence[InjectionInput],
    *,
    parameters: Mapping[str, Any],
    reference_injection_ids: Mapping[str, int] | None = None,
) -> AlignmentOutput:
    """Align per-injection features in RT, then group them without cross-polarity merges."""
    if not features or not injections:
        raise LcmsAlignmentError("ALIGNMENT_EMPTY_INPUT", "Aucune injection ou feature à aligner.")
    mass_tolerance = _positive(parameters.get("anchor_mass_tolerance_ppm", 8.0), "anchor_mass_tolerance_ppm")
    initial_rt_tolerance = _positive(parameters.get("max_initial_rt_delta_seconds", 120.0), "max_initial_rt_delta_seconds")
    residual_limit = _positive(parameters.get("max_anchor_residual_seconds", 20.0), "max_anchor_residual_seconds")
    min_anchors = int(parameters.get("minimum_anchors", 8))
    min_coverage = _finite(parameters.get("minimum_anchor_rt_coverage", 0.5), "minimum_anchor_rt_coverage")
    group_ppm = _positive(parameters.get("group_mass_tolerance_ppm", 10.0), "group_mass_tolerance_ppm")
    group_rt = _positive(parameters.get("group_rt_tolerance_seconds", 10.0), "group_rt_tolerance_seconds")
    min_injections = int(parameters.get("minimum_injections", 3))
    if min_anchors < 4 or min_anchors > 500:
        raise LcmsAlignmentError("INVALID_ALIGNMENT_PARAMETER", "minimum_anchors doit être compris entre 4 et 500.")
    if min_injections < 3 or min_injections > 200:
        raise LcmsAlignmentError("INVALID_ALIGNMENT_PARAMETER", "minimum_injections doit être compris entre 3 et 200.")
    if not 0.1 <= min_coverage <= 1.0:
        raise LcmsAlignmentError("INVALID_ALIGNMENT_PARAMETER", "minimum_anchor_rt_coverage doit être dans [0.1, 1].")
    if len({item.injection_id for item in injections}) < min_injections:
        raise LcmsAlignmentError(
            "ALIGNMENT_INSUFFICIENT_INJECTIONS",
            f"Au moins {min_injections} injections distinctes sont requises.",
        )
    injection_ids = {item.injection_id for item in injections}
    if any(item.injection_id not in injection_ids for item in features):
        raise LcmsAlignmentError("ALIGNMENT_INJECTION_MISMATCH", "Une feature référence une injection non sélectionnée.")
    if len({item.feature_id for item in features}) != len(features):
        raise LcmsAlignmentError("ALIGNMENT_DUPLICATE_FEATURE", "Identifiants de features dupliqués.")
    for feature in features:
        if feature.polarity not in {"positive", "negative"}:
            raise LcmsAlignmentError(
                "ALIGNMENT_UNKNOWN_POLARITY",
                "La Phase 1J refuse l'alignement des features de polarité inconnue.",
            )
        _positive(feature.precursor_mz, "precursor_mz")
        rt = _finite(feature.retention_time_seconds, "retention_time_seconds")
        if rt < 0:
            raise LcmsAlignmentError("INVALID_ALIGNMENT_INPUT", "Le temps de rétention ne peut pas être négatif.")

    references, reference_methods = _select_reference_injections(
        features, injections, requested_references=reference_injection_ids
    )
    mapped: dict[int, float | None] = {}
    transformations: dict[str, Any] = {}
    run_summaries: dict[str, Any] = {}
    by_injection_polarity: dict[tuple[int, str], list[FeatureInput]] = defaultdict(list)
    for feature in features:
        by_injection_polarity[(feature.injection_id, feature.polarity)].append(feature)
    all_polarities = sorted({item.polarity for item in features})

    injection_by_id = {item.injection_id: item for item in injections}
    for polarity in all_polarities:
        reference_id = references.get(polarity)
        if reference_id is None:
            continue
        reference_features = by_injection_polarity[(reference_id, polarity)]
        ref_rt_values = [item.retention_time_seconds for item in reference_features]
        ref_min, ref_max = min(ref_rt_values), max(ref_rt_values)
        if ref_max <= ref_min:
            raise LcmsAlignmentError(
                "ALIGNMENT_REFERENCE_RT_RANGE",
                f"La référence {reference_id} en {polarity} ne couvre pas une plage RT exploitable.",
            )
        for feature in reference_features:
            mapped[feature.feature_id] = feature.retention_time_seconds
        transformations[f"{polarity}:{reference_id}"] = {
            "reference_injection_id": reference_id,
            "method": "identity_reference",
            "anchor_count": len(reference_features),
            "reference_method": reference_methods[polarity],
            "source_rt_min_seconds": ref_min,
            "source_rt_max_seconds": ref_max,
        }

        for injection in sorted(injections, key=lambda item: (item.injection_order, item.injection_id)):
            injection_id = injection.injection_id
            if injection_id == reference_id:
                continue
            current = by_injection_polarity.get((injection_id, polarity), [])
            if not current:
                run_summaries[str(injection_id)] = {
                    **run_summaries.get(str(injection_id), {}),
                    polarity: {"status": "no_features_for_polarity", "feature_count": 0},
                }
                continue
            anchors = _unique_anchor_pairs(
                current,
                reference_features,
                mass_tolerance_ppm=mass_tolerance,
                max_initial_rt_delta_seconds=initial_rt_tolerance,
            )
            knots, knot_summary = _prepare_monotonic_knots(
                anchors,
                max_anchor_residual_seconds=residual_limit,
                minimum_anchors=min_anchors,
            )
            current_rts = [item.retention_time_seconds for item in current]
            current_span = max(current_rts) - min(current_rts)
            source_coverage = (
                (knots[-1][0] - knots[0][0]) / current_span if current_span > 0 else 0.0
            )
            ref_coverage = (
                (knots[-1][1] - knots[0][1]) / (ref_max - ref_min)
                if ref_max > ref_min else 0.0
            )
            if source_coverage < min_coverage or ref_coverage < min_coverage:
                raise LcmsAlignmentError(
                    "ALIGNMENT_ANCHOR_COVERAGE",
                    f"Repères insuffisamment répartis pour l'injection {injection_id} ({polarity}) : "
                    f"couverture source={source_coverage:.3f}, référence={ref_coverage:.3f}, "
                    f"minimum={min_coverage:.3f}.",
                )
            mapped_count = 0
            for feature in current:
                value = _piecewise_transform(feature.retention_time_seconds, knots)
                mapped[feature.feature_id] = value
                if value is not None:
                    mapped_count += 1
            transformations[f"{polarity}:{injection_id}"] = {
                "reference_injection_id": reference_id,
                "method": "mutual_unique_mz_anchors_piecewise_linear",
                "reference_method": reference_methods[polarity],
                **knot_summary,
                "source_rt_min_seconds": min(current_rts),
                "source_rt_max_seconds": max(current_rts),
                "source_anchor_coverage": source_coverage,
                "reference_anchor_coverage": ref_coverage,
                "mapped_feature_count": mapped_count,
                "outside_anchor_range_feature_count": len(current) - mapped_count,
            }
            run_summaries.setdefault(str(injection_id), {})[polarity] = {
                "status": "aligned",
                "feature_count": len(current),
                "anchor_count": len(anchors),
                "mapped_feature_count": mapped_count,
                "outside_anchor_range_feature_count": len(current) - mapped_count,
            }

    expected_injection_counts = {
        polarity: len({
            item.injection_id for item in features if item.polarity == polarity
        })
        for polarity in all_polarities
    }
    groups = _group_aligned_features(
        features,
        mapped,
        expected_injection_counts=expected_injection_counts,
        group_mass_tolerance_ppm=group_ppm,
        group_rt_tolerance_seconds=group_rt,
    )
    unaligned_feature_count = sum(value is None for value in mapped.values())
    return AlignmentOutput(
        groups=tuple(groups),
        diagnostics={
            "protocol_version": ALIGNMENT_PROTOCOL_VERSION,
            "reference_injection_ids": references,
            "reference_selection_methods": reference_methods,
            "transformations": transformations,
            "injection_summaries": run_summaries,
            "source_feature_count": len(features),
            "mapped_feature_count": sum(value is not None for value in mapped.values()),
            "unaligned_feature_count": unaligned_feature_count,
            "consensus_feature_count": len(groups),
            "singleton_feature_count": sum(item["alignment_status"] == "singleton" for item in groups),
            "partial_feature_count": sum(item["alignment_status"] == "partial" for item in groups),
            "expected_injection_counts_by_polarity": expected_injection_counts,
            "limitations": [
                "Alignment is performed independently by ionization polarity.",
                "Anchor identity is inferred from mutually unique m/z/RT candidates and known charge/adduct metadata.",
                "Piecewise retention-time maps are not extrapolated outside the observed anchor interval.",
                "Features outside anchor coverage remain explicit singleton features.",
                "This implementation does not perform isotope/adduct collapsing or cross-polarity merging.",
                "Cluster construction is deterministic and allows at most one source feature per injection in a consensus group.",
            ],
        },
    )
