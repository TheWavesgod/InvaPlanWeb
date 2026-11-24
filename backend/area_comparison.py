"""Compare three fixed 39-species portfolios on the fractional Jiaxing city mask."""
from dataclasses import dataclass
from hashlib import file_digest, sha256
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request

from .climate import SCENARIOS
from .datasets import Grid
from .prepare_area_comparison import cell_areas
from .sample import FULL_RELEASE_ID, Sample


CITY_ID = "330400"
# Row-major (row, col) integer pairs independently verified against the official 62-city zone raster.
JIAXING_SUPPORT_SHA256 = "5567dc3444b364a2d99566c6347e526b854c0ad228199866ed65c68e24b811c8"
HOLDOUTS = ("ssp126", "ssp245", "ssp585")
Holdout = Literal["ssp126", "ssp245", "ssp585"]
BUDGETS = (5, 10, 20)
STATES = ("persistent", "gain")
router = APIRouter(prefix="/api/area-comparisons")


def habitat_masks(baseline, future_min, cutoffs):
    suitable = baseline >= cutoffs[:, None]
    future_suitable = future_min >= cutoffs[:, None]
    return np.stack((suitable & future_suitable, ~suitable & future_suitable))


@dataclass(frozen=True)
class Fold:
    scores: dict[str, np.ndarray]
    drivers: np.ndarray  # state × species × cell, based on training scenarios
    habitat: np.ndarray  # same axes, based on both held-out periods


def evaluate_fold(baseline, future, cutoffs, train, test):
    training = future[train]
    level = training.mean(axis=(0, 1))
    train_min = training.min(axis=0)
    eligible = habitat_masks(baseline, train_min, cutoffs)
    signals = (np.minimum(baseline, train_min), train_min - baseline)
    ranks = np.zeros(eligible.shape, dtype=np.float64)
    for state, signal in enumerate(signals):
        for species, mask in enumerate(eligible[state]):
            values = signal[species, mask]
            if values.size:
                ranks[state, species, mask] = np.searchsorted(np.sort(values), values, side="right") / values.size
    site = ranks.max(axis=(0, 1))
    return Fold(
        scores={"level": level, "change": level - baseline.mean(axis=0), "site": site},
        drivers=eligible & (ranks == site),
        habitat=habitat_masks(baseline, future[test].min(axis=0), cutoffs),
    )


def allocate(areas, order, budget):
    target = math.fsum(areas) * budget / 100
    tolerance = 8 * math.ulp(target)
    allocated = np.zeros_like(areas)
    spent = compensation = 0.0
    for position in order:
        remaining = target - spent
        if remaining <= tolerance:
            break
        area = areas[position]
        if area - remaining > tolerance:
            allocated[position] = remaining
            break
        allocated[position] = area
        # Compensated accumulation preserves whole-cell budget boundaries.
        increment = area - compensation
        updated = spent + increment
        compensation = (updated - spent) - increment
        spent = updated
    return allocated


def coverage(habitat, areas):
    return np.einsum("tsn,n->ts", habitat, areas, dtype=np.float64)


def portfolio(fold, areas, species_ids, orders, budget):
    totals = coverage(fold.habitat, areas)
    species = {key: {"persistentKm2": totals[0, i].item(), "gainKm2": totals[1, i].item()}
               for i, key in enumerate(species_ids)}
    allocations, summary = {}, {}
    for rule, order in orders.items():
        selected = allocate(areas, order, budget)
        allocations[rule] = selected.tolist()
        captured = coverage(fold.habitat, selected)
        # Each species with any citywide habitat receives the same weight.
        metrics = {}
        for state, name in enumerate(STATES):
            present = totals[state] > 0
            metrics[name + "Pct"] = (float(100 * np.mean(captured[state, present] / totals[state, present]))
                                     if present.any() else None)
        metrics["gainSpecies"] = int(np.count_nonzero(captured[1]))
        summary[rule] = metrics
        for i, key in enumerate(species_ids):
            species[key][rule] = {"persistentKm2": captured[0, i].item(), "gainKm2": captured[1, i].item()}
    return {"budget": budget, "allocations": allocations, "summary": summary,
            "eligibleGainSpecies": int(np.count_nonzero(totals[1])), "species": species}


class AreaComparison:
    def __init__(self, meta, grid, indices, areas, baseline, future, cutoffs, version):
        self.grid = grid
        self.indices = indices
        self.positions = {int(flat): i for i, flat in enumerate(indices)}
        self.species_ids = meta["speciesIds"]
        self.species_index = {key: i for i, key in enumerate(self.species_ids)}
        self.scenarios = meta["scenarios"]
        self.baseline, self.future, self.cutoffs = baseline, future, cutoffs
        self.folds, self.portfolios = {}, {}
        common = {"version": version, "cityId": CITY_ID, "grid": grid.model_dump(mode="json"),
                  "indices": indices.tolist(), "areas": areas.tolist()}
        for holdout in HOLDOUTS:
            test = np.array([key.split("-", 1)[0] == holdout for key in self.scenarios])
            fold = evaluate_fold(baseline, future, cutoffs, ~test, test)
            self.folds[holdout] = fold
            # Inputs are sorted by native row and column; stable sorting preserves this tie-break.
            orders = {name: np.argsort(-score, kind="stable") for name, score in fold.scores.items()}
            for budget in BUDGETS:
                self.portfolios[budget, holdout] = {
                    **common, "holdout": holdout, **portfolio(fold, areas, self.species_ids, orders, budget),
                }

    def comparison(self, budget, holdout):
        return self.portfolios[budget, holdout]

    def habitat(self, species_id, holdout):
        states = self.folds[holdout].habitat[:, self.species_index[species_id]]
        return {name: self.indices[mask].tolist() for name, mask in zip(STATES, states, strict=True)}

    def cell(self, row, col, species_id, holdout):
        y, x = row - self.grid.row0, col - self.grid.col0
        if not (0 <= y < self.grid.height and 0 <= x < self.grid.width):
            raise KeyError((row, col))
        pos = self.positions[y * self.grid.width + x]
        i = self.species_index[species_id]
        fold = self.folds[holdout]
        drivers = [{"speciesId": self.species_ids[s], "state": STATES[state]}
                   for s, state in np.argwhere(fold.drivers[:, :, pos].T)]
        return {"drivers": drivers, "scores": {key: float(scores[pos]) for key, scores in fold.scores.items()},
                "speciesId": species_id, "baseline": float(self.baseline[i, pos]), "threshold": float(self.cutoffs[i]),
                "future": {key: float(self.future[k, i, pos]) for k, key in enumerate(self.scenarios)}}


def verify_grid(grid, indices, sample):
    west, south, east, north = sample.bounds
    dx, dy = (east - west) / sample.width, (north - south) / sample.height
    left, top = west + (grid.col0 - sample.col0) * dx, north - (grid.row0 - sample.row0) * dy
    expected = (left, top - grid.height * dy, left + grid.width * dx, top)
    if not np.allclose(grid.bounds, expected, rtol=0, atol=1e-8):
        raise ValueError("The city mask and active release use different native grids.")
    if (indices.ndim != 1 or indices.dtype.kind not in "iu" or not indices.size
            or indices[0] < 0 or indices[-1] >= grid.width * grid.height or (indices[:-1] >= indices[1:]).any()):
        raise ValueError("City cell indices must be unique, row-major and inside the city grid.")
    y, x = np.divmod(indices.astype(np.int64), grid.width)
    y += grid.row0 - sample.row0
    x += grid.col0 - sample.col0
    overlap = (y >= 0) & (y < sample.height) & (x >= 0) & (x < sample.width)
    if not overlap.any():
        raise ValueError("The Jiaxing city data must intersect the verified sample.")
    return overlap, y[overlap], x[overlap]


def verify_city_support(grid, indices, areas):
    y, x = np.divmod(indices.astype(np.int64), grid.width)
    coordinates = np.column_stack((grid.row0 + y, grid.col0 + x)).astype("<i4")
    if sha256(coordinates.tobytes()).hexdigest() != JIAXING_SUPPORT_SHA256:
        raise ValueError("The native cells do not match the accepted Jiaxing municipal support.")
    if not np.allclose(areas, cell_areas(grid, indices), atol=1e-10, rtol=0):
        raise ValueError("Stored city cell areas disagree with WGS84 geometry.")


def load_area_comparison(directory: Path, sample: Sample, climate: dict):
    path = directory / f"area-{CITY_ID}.npz"
    if sample.dataset.release_id != FULL_RELEASE_ID or not path.is_file():
        return None
    with path.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        with np.load(stream, allow_pickle=False) as archive:
            meta = json.loads(archive["meta"].item())
            indices, areas, baseline, future, cutoffs = (
                archive[key] for key in ("indices", "areas", "baseline", "future", "thresholds"))
    ids = list(sample.species_index)
    scenarios = meta["scenarios"]
    if (sample.maxsss is None or meta["schema"] != 1 or meta["cityId"] != CITY_ID
            or ids != [f"PROD{i:04}" for i in range(1, 40)] or ids != meta["speciesIds"]
            or meta["releaseVersion"] != sample.data_version or len(scenarios) != 6
            or set(scenarios) != set(SCENARIOS) or set(climate) != set(SCENARIOS)
            or not np.array_equal(cutoffs, sample.thresholds)):
        raise ValueError("City inputs disagree with the verified 39-species release or its thresholds.")
    grid = Grid.model_validate(meta["grid"])
    overlap, y, x = verify_grid(grid, indices, sample)
    if (areas.shape != indices.shape or not (np.isfinite(areas) & (areas > 0)).all()
            or baseline.shape != (len(ids), indices.size) or future.shape != (6, *baseline.shape)
            or any(a.dtype != np.float32 or not ((a >= 0) & (a <= 1)).all() for a in (baseline, future))
            or areas.dtype != np.float64 or cutoffs.dtype != np.float64):
        raise ValueError("City predictions and areas must be complete on shared support and use the declared scale.")
    verify_city_support(grid, indices, areas)
    if not np.array_equal(baseline[:, overlap], sample.values[:, y, x]) or any(
        not np.array_equal(future[k][:, overlap], climate[key][0][:, y, x]) for k, key in enumerate(scenarios)
    ):
        raise ValueError("Shared native cells disagree with the verified baseline or future release.")
    version = f"city-area-v1-{digest[:16]}"
    return AreaComparison(meta, grid, indices, areas, baseline.astype(np.float64), future.astype(np.float64),
                          cutoffs, version)


def active_area(request, city_id):
    area = request.app.state.area_comparison
    if city_id != CITY_ID or area is None:
        raise HTTPException(404, "area_comparison_unavailable")
    return area


@router.get("/{city_id}")
def compare(city_id: str, request: Request, budget: int = 10, holdout: Holdout = "ssp585"):
    area = active_area(request, city_id)
    if budget not in BUDGETS:
        raise HTTPException(422, "invalid_area_budget")
    return area.comparison(budget, holdout)


@router.get("/{city_id}/species/{species_id}")
def species_habitat(city_id: str, species_id: str, request: Request, holdout: Holdout = "ssp585"):
    area = active_area(request, city_id)
    try:
        return area.habitat(species_id, holdout)
    except KeyError:
        raise HTTPException(404, "area_species_unknown") from None


@router.get("/{city_id}/cells/{row}/{col}")
def inspect_city_cell(city_id: str, row: int, col: int, species_id: str, request: Request, holdout: Holdout = "ssp585"):
    area = active_area(request, city_id)
    if species_id not in area.species_index:
        raise HTTPException(404, "area_species_unknown")
    try:
        return area.cell(row, col, species_id, holdout)
    except KeyError:
        raise HTTPException(404, "cell_outside_city_support") from None
