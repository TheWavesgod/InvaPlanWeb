"""Deterministic count-based ranking for the baseline pilot."""

from dataclasses import dataclass

import numpy as np

from .sample import Sample


@dataclass(frozen=True)
class Evaluation:
    focused: np.ndarray
    complete: np.ndarray
    excluded: np.ndarray
    counts: np.ndarray
    values: np.ndarray
    species_ids: list[str]


def evaluate(sample: Sample, species_ids: list[str], excluded_ids: list[str]) -> Evaluation:
    indices = [sample.species_index[code] for code in species_ids]
    values = sample.values[indices].reshape(len(indices), -1)
    finite = np.isfinite(values)
    focused = finite & (values.astype(np.float64) >= sample.thresholds[indices, None])
    excluded = np.zeros(values.shape[1], dtype=bool)
    excluded[[sample.index_by_id[code] for code in excluded_ids]] = True
    return Evaluation(focused, finite.all(axis=0), excluded, focused.sum(axis=0), values, species_ids)


def focus_ids(evidence: Evaluation, index: int) -> list[str]:
    return [code for code, is_high in zip(evidence.species_ids, evidence.focused[:, index]) if is_high]


def cell_centres(sample: Sample, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.divmod(indices, sample.width)
    west, south, east, north = sample.bounds
    lat = north - (rows + 0.5) * (north - south) / sample.height
    lon = west + (cols + 0.5) * (east - west) / sample.width
    return np.radians(lat), np.radians(lon)


def distance_km(lat, lon, other_lat, other_lon):
    haversine = (np.sin((lat - other_lat) / 2) ** 2
                 + np.cos(lat) * np.cos(other_lat) * np.sin((lon - other_lon) / 2) ** 2)
    return 12742.0 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))


def site_distances(sample: Sample, indices: np.ndarray) -> np.ndarray:
    lat, lon = cell_centres(sample, indices)
    distances = distance_km(lat[:, None], lon[:, None], lat, lon)
    np.fill_diagonal(distances, np.inf)
    return distances


def median_nearest_km(distances: np.ndarray) -> float | None:
    return float(np.median(distances.min(axis=1))) if len(distances) >= 2 else None


def shortlist_summary(sample: Sample, evidence: Evaluation, cell_ids: list[str]) -> dict:
    indices = np.array([sample.index_by_id[key] for key in cell_ids], dtype=int)
    counts = evidence.focused[:, indices].sum(axis=1).tolist()
    return {
        "cellCount": len(cell_ids),
        "speciesCellCounts": dict(zip(evidence.species_ids, counts, strict=True)),
        "coveredSpeciesIds": [code for code, n in zip(evidence.species_ids, counts, strict=True) if n],
        "medianNearestKm": median_nearest_km(site_distances(sample, indices)),
        "demoDerivedCellCount": sum(key in sample.demo_only for key in cell_ids) if sample.maxsss else None,
    }


def spread_selection(sample: Sample, eligible: np.ndarray, counts: np.ndarray, k: int) -> np.ndarray:
    # eligible is in native ID order. Start at the lowest ID with the highest score.
    lat, lon = cell_centres(sample, eligible)
    remaining = counts[eligible].copy()
    nearest = np.full(eligible.size, np.inf)
    selected = []
    for _ in range(min(k, eligible.size)):
        tied = np.flatnonzero(remaining == remaining.max())
        # Equal distances to the nearest selected centre resolve by native ID.
        pick = tied[np.argmax(np.round(nearest[tied], 9))]
        selected.append(eligible[pick])
        remaining[pick] = -1
        nearest = np.minimum(nearest, distance_km(lat, lon, lat[pick], lon[pick]))
    return np.asarray(selected, dtype=int)


def build_run(sample: Sample, evidence: Evaluation, inputs: dict) -> dict:
    ranked = evidence.complete & ~evidence.excluded
    eligible = np.flatnonzero(ranked & (evidence.counts > 0))
    scores = np.where(ranked, 100 * evidence.counts / len(evidence.species_ids), np.nan)
    if inputs.get("tieBreak") == "spatial_spread":
        selected = spread_selection(sample, eligible, evidence.counts, inputs["k"])
    else:
        order = np.lexsort((eligible, -evidence.counts[eligible]))
        selected = eligible[order[:inputs["k"]]]
    distances = site_distances(sample, selected)
    recommendations = [
        {**sample.coordinates(int(i)), "score": float(scores[i]),
         "c": int(evidence.counts[i]), "focusIds": focus_ids(evidence, int(i))}
        for i in selected
    ]
    if inputs.get("tieBreak") == "spatial_spread":
        for rank in range(1, len(recommendations)):
            recommendations[rank]["nearestEarlierKm"] = float(distances[rank, :rank].min())
    return {
        "input": inputs, "dataVersion": sample.data_version, "policyVersion": sample.policy_version,
        "releaseId": sample.dataset.release_id,
        "rankedCount": int(ranked.sum()),
        "incompleteCount": int((~evidence.complete & ~evidence.excluded).sum()),
        "excludedCount": int(evidence.excluded.sum()),
        "positiveCount": int(len(eligible)),
        "medianNearestKm": median_nearest_km(distances),
        "recommendations": recommendations,
        "scores": [float(score) if np.isfinite(score) else None for score in scores],
    }


def inspect_cell(sample: Sample, evidence: Evaluation, index: int) -> dict:
    flat_values = sample.values.reshape(len(sample.dataset.species), -1)[:, index]
    missing = [code for code, v in zip(evidence.species_ids, evidence.values[:, index]) if not np.isfinite(v)]
    count = int(evidence.counts[index])
    excluded = bool(evidence.excluded[index])
    return {
        "cell": sample.coordinates(index),
        "values": {
            item.id: float(value) if np.isfinite(value) else None
            for item, value in zip(sample.dataset.species, flat_values, strict=True)
        },
        "score": None if missing or excluded else 100 * count / len(evidence.species_ids),
        "c": count, "focusIds": focus_ids(evidence, index), "missingIds": missing, "excluded": excluded,
    }
