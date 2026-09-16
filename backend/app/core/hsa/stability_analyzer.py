"""HSA: transparent sequence-stability index, not a clinical predictor.

The module combines established sequence descriptors with an explicit bounded
index. It does not estimate mutation probability, quantum energy, or a full
duplex free energy for long genomic DNA.
"""
from collections import Counter
from typing import Dict, List, Optional
import math


def analyze_stability(sequence: str, env_factors: Optional[Dict] = None) -> Dict:
    """Return a bounded exploratory index, not a mutation probability.

    The pH is reported but excluded because no buffer, salt, modification, or
    pKa model is supplied. Nearest-neighbor Tm is used only for oligo windows.
    """
    env = env_factors or {}
    temperature = float(env.get("temperature", 37.0))
    ph = float(env.get("ph", 7.4))
    stress_factor = max(0.0, float(env.get("stress_factor", 1.0)))
    raw = "".join((sequence or "").upper().replace("U", "T"))
    if not raw or any(char not in "ATCG" for char in raw):
        return {"success": False, "error": "HSA exige une séquence ADN/ARN composée uniquement de A, T/U, C et G"}
    seq = raw

    gc_fraction = (seq.count("G") + seq.count("C")) / len(seq)
    entropy_bits = _shannon_entropy(seq)
    entropy_normalized = min(1.0, entropy_bits / 2.0)
    gc_skew = _gc_skew(seq)
    windows = _window_metrics(seq, min(20, len(seq)))
    tm_values = [item["tm_celsius"] for item in windows if item["tm_celsius"] is not None]
    median_tm = _median(tm_values)
    thermal_margin = _sigmoid((median_tm - temperature) / 5.0) if median_tm is not None else 0.5
    local_heterogeneity = _local_heterogeneity(windows)
    vulnerability = 100.0 * (
        0.30 * (1.0 - entropy_normalized)
        + 0.20 * min(1.0, abs(gc_skew))
        + 0.25 * (1.0 - thermal_margin)
        + 0.25 * local_heterogeneity
    )
    vulnerability = min(100.0, vulnerability * min(stress_factor, 2.0))
    global_score = round(max(0.0, min(100.0, 100.0 - vulnerability)))
    profile = _profile(windows, temperature, stress_factor)

    return {
        "success": True,
        "method": "HSA",
        "method_full": "Heuristic Stability Analysis",
        "formula_version": "HSA-2.0-transparent-index",
        "disclaimer": "Indice heuristique non validé comme prédicteur de mutation ou de stabilité clinique.",
        "sequence_length": len(seq),
        "gc_content": round(gc_fraction * 100.0, 3),
        "gc_skew": round(gc_skew, 5),
        "shannon_entropy_bits": round(entropy_bits, 5),
        "entropy_normalized": round(entropy_normalized, 5),
        "hydrogen_bond_count_upper_bound": seq.count("G") * 3 + seq.count("C") * 3 + (seq.count("A") + seq.count("T")) * 2,
        "nearest_neighbor_tm_celsius": round(median_tm, 3) if median_tm is not None else None,
        "thermal_margin": round(thermal_margin, 5),
        "local_heterogeneity": round(local_heterogeneity, 5),
        "stability_profile": profile,
        "fragile_zones": [item for item in profile if item["stability"] < 40],
        "vulnerability_index": round(vulnerability, 3),
        "mutation_prob": None,
        "global_score": global_score,
        "heuristic_index": global_score,
        "component_contributions": {
            "low_complexity": round(0.30 * (1.0 - entropy_normalized), 5),
            "gc_skew": round(0.20 * min(1.0, abs(gc_skew)), 5),
            "thermal_margin": round(0.25 * (1.0 - thermal_margin), 5),
            "local_heterogeneity": round(0.25 * local_heterogeneity, 5),
        },
        "validity_domain": {
            "sequence_type": "DNA/RNA nucleotide sequence",
            "nn_tm_window_model": "8-20 nt windows",
            "salt_conditions": "fixed Biopython Tm_NN defaults used by this implementation",
            "clinical_validation": False,
        },
        "environmental": {"temperature": temperature, "ph": ph, "stress_factor": stress_factor, "ph_used_in_formula": False},
        "interpretation": _interpret_score(global_score),
        "references": ["SantaLucia 1998, DOI:10.1073/pnas.95.4.1460", "Vinga 2014, DOI:10.1093/bib/bbt053"],
    }


def _shannon_entropy(sequence: str) -> float:
    counts = Counter(sequence)
    return -sum((count / len(sequence)) * math.log2(count / len(sequence)) for count in counts.values())


def _gc_skew(sequence: str) -> float:
    denominator = sequence.count("G") + sequence.count("C")
    return (sequence.count("G") - sequence.count("C")) / denominator if denominator else 0.0


def _nearest_neighbor_tm(sequence: str) -> Optional[float]:
    if len(sequence) < 8:
        return None
    try:
        from Bio.SeqUtils import MeltingTemp as mt
        return float(mt.Tm_NN(sequence, nn_table=mt.DNA_NN3, Na=50.0, Mg=0.0, dNTPs=0.0, dnac1=250.0, dnac2=250.0))
    except (ImportError, ValueError, TypeError):
        return None


def _window_metrics(sequence: str, window_size: int) -> List[Dict]:
    step = max(1, window_size // 2)
    metrics = []
    for start in range(0, max(1, len(sequence) - window_size + 1), step):
        window = sequence[start:start + window_size]
        if len(window) < max(8, window_size // 2):
            continue
        tm_value = _nearest_neighbor_tm(window)
        metrics.append({"start": start, "end": start + len(window), "gc_content": round((window.count("G") + window.count("C")) / len(window) * 100.0, 3), "tm_celsius": round(tm_value, 3) if tm_value is not None else None})
    return metrics


def _local_heterogeneity(windows: List[Dict]) -> float:
    gc_values = [item["gc_content"] / 100.0 for item in windows]
    tm_values = [item["tm_celsius"] for item in windows if item["tm_celsius"] is not None]
    gc_range = max(gc_values) - min(gc_values) if gc_values else 0.0
    tm_range = min(1.0, (max(tm_values) - min(tm_values)) / 50.0) if tm_values else 0.0
    return min(1.0, 0.5 * gc_range + 0.5 * tm_range)


def _profile(windows: List[Dict], temperature: float, stress_factor: float) -> List[Dict]:
    profile = []
    for index, window in enumerate(windows, start=1):
        tm = window["tm_celsius"]
        margin = _sigmoid((tm - temperature) / 5.0) if tm is not None else 0.5
        profile.append({**window, "region": index, "stability": round(max(0.0, min(100.0, 100.0 * margin / max(1.0, stress_factor))), 3)})
    return profile


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def _interpret_score(score: int) -> str:
    if score >= 80:
        return "Indice de stabilité élevé selon HSA; aucune probabilité biologique."
    if score >= 60:
        return "Indice intermédiaire selon HSA; interprétation exploratoire."
    if score >= 40:
        return "Indice modéré selon HSA; hétérogénéité locale à examiner."
    if score >= 20:
        return "Indice faible selon HSA; vérification expérimentale nécessaire."
    return "Indice très faible selon HSA; aucune conclusion clinique."
