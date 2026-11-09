"""Deterministic count-based ranking for the baseline pilot."""

from dataclasses import dataclass

import numpy as np

from .sample import POLICY_VERSION, SPECIES, SPECIES_INDEX, Sample


@dataclass(frozen=True)
class Evaluation:
    focused: np.ndarray
    complete: np.ndarray
    excluded: np.ndarray
    counts: np.ndarray
    values: np.ndarray
    species_ids: list[str]


def evaluate(sample: Sample, species_ids: list[str], excluded_ids: list[str]) -> Evaluation:
    indices = [SPECIES_INDEX[code] for code in species_ids]
    values = sample.values[indices].reshape(len(indices), -1)
    finite = np.isfinite(values)
    focused = finite & (values.astype(np.float64) >= sample.thresholds[indices, None])
    excluded = np.zeros(values.shape[1], dtype=bool)
    excluded[[sample.index_by_id[code] for code in excluded_ids]] = True
    return Evaluation(focused, finite.all(axis=0), excluded, focused.sum(axis=0), values, species_ids)


def focus_ids(evidence: Evaluation, index: int) -> list[str]:
    return [code for code, is_high in zip(evidence.species_ids, evidence.focused[:, index]) if is_high]


def build_run(sample: Sample, evidence: Evaluation, inputs: dict) -> dict:
    ranked = evidence.complete & ~evidence.excluded
    eligible = np.flatnonzero(ranked & (evidence.counts > 0))
    order = np.lexsort((eligible, -evidence.counts[eligible]))
    scores = np.where(ranked, 100 * evidence.counts / len(evidence.species_ids), np.nan)
    return {
        "input": inputs, "dataVersion": sample.data_version, "policyVersion": POLICY_VERSION,
        "rankedCount": int(ranked.sum()),
        "incompleteCount": int((~evidence.complete & ~evidence.excluded).sum()),
        "excludedCount": int(evidence.excluded.sum()),
        "positiveCount": int(len(eligible)),
        "recommendations": [
            {**sample.coordinates(int(i)), "score": float(scores[i]), "c": int(evidence.counts[i]),
             "focusIds": focus_ids(evidence, int(i))}
            for i in eligible[order[:inputs["k"]]]
        ],
        "scores": [float(score) if np.isfinite(score) else None for score in scores],
    }


def inspect_cell(sample: Sample, evidence: Evaluation, index: int) -> dict:
    flat_values = sample.values.reshape(len(SPECIES), -1)[:, index]
    missing = [code for code, v in zip(evidence.species_ids, evidence.values[:, index]) if not np.isfinite(v)]
    count = int(evidence.counts[index])
    excluded = bool(evidence.excluded[index])
    return {
        "cell": sample.coordinates(index),
        "values": {code: float(v) if np.isfinite(v) else None for (code, _, _), v in zip(SPECIES, flat_values)},
        "score": None if missing or excluded else 100 * count / len(evidence.species_ids),
        "c": count, "focusIds": focus_ids(evidence, index), "missingIds": missing, "excluded": excluded,
    }
