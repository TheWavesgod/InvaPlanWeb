"""Compare three 39-species portfolios within each city's verified native support."""
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import file_digest, sha256
import json
import logging
import math
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Annotated, Literal
from zipfile import BadZipFile

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rasterio.features import shapes
from rasterio.transform import from_bounds

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
logger = logging.getLogger(__name__)


class IndexModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


Percentage = Annotated[float, Field(ge=0, le=100)]
SpeciesCount = Annotated[int, Field(ge=0, le=39)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Strategy = Literal["level", "change", "site"]


class Metrics(IndexModel):
    persistentPct: Percentage | None
    gainPct: Percentage | None
    gainSpecies: SpeciesCount


class CityResult(IndexModel):
    summary: dict[Strategy, Metrics] = Field(min_length=3)
    eligibleGainSpecies: SpeciesCount

    @model_validator(mode="after")
    def check_counts(self):
        for metrics in self.summary.values():
            if (metrics.gainSpecies > self.eligibleGainSpecies
                    or (metrics.gainPct is None) != (self.eligibleGainSpecies == 0)):
                raise ValueError("City gain metrics disagree with the eligible species count.")
        return self


BudgetResults = Annotated[dict[Literal["5", "10", "20"], CityResult], Field(min_length=3)]


class CityEntry(IndexModel):
    cityId: str = Field(pattern=r"^[0-9]{6}$")
    nameZh: str = Field(min_length=1)
    nameEn: str = Field(min_length=1)
    cellCount: int = Field(gt=0)
    areaKm2: float = Field(gt=0)
    file: str = Field(min_length=1)
    sha256: Digest
    supportSha256: Digest
    results: dict[Holdout, BudgetResults] = Field(min_length=3)

    def overview(self, budget, holdout):
        return {
            "cityId": self.cityId, "nameZh": self.nameZh, "nameEn": self.nameEn,
            "cellCount": self.cellCount, "areaKm2": self.areaKm2, "budgetKm2": self.areaKm2 * budget / 100,
            **self.results[holdout][str(budget)].model_dump(),
        }


class CityIndex(IndexModel):
    schema_version: Literal[1] = Field(alias="schema")
    releaseVersion: str
    thresholdTableSha256: Digest
    cities: list[CityEntry] = Field(min_length=1)


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
        common = {"version": version, "cityId": meta["cityId"], "grid": grid.model_dump(mode="json"),
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


def verify_grid(grid, indices, sample, city_id=CITY_ID):
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
    if city_id == CITY_ID and not overlap.any():
        raise ValueError("The Jiaxing city data must intersect the verified sample.")
    return overlap, y[overlap], x[overlap]


def verify_city_support(grid, indices, areas, expected=JIAXING_SUPPORT_SHA256):
    y, x = np.divmod(indices.astype(np.int64), grid.width)
    coordinates = np.column_stack((grid.row0 + y, grid.col0 + x)).astype("<i4")
    if sha256(coordinates.tobytes()).hexdigest() != expected:
        raise ValueError("The native cells do not match the accepted municipal support.")
    if not np.allclose(areas, cell_areas(grid, indices), atol=1e-10, rtol=0):
        raise ValueError("Stored city cell areas disagree with WGS84 geometry.")


def check_release(sample, climate):
    if (sample.dataset.release_id != FULL_RELEASE_ID or sample.maxsss is None
            or list(sample.species_index) != [f"PROD{i:04}" for i in range(1, 40)]
            or set(climate) != set(SCENARIOS)):
        raise ValueError("Area comparisons require the verified 39-species baseline, thresholds and climate.")


def read_city(path, sample, climate, entry=None):
    city_id = entry.cityId if entry else CITY_ID
    with path.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
        if entry and digest != entry.sha256:
            raise ValueError("City inputs disagree with the manifest SHA-256.")
        stream.seek(0)
        with np.load(stream, allow_pickle=False) as archive:
            meta = json.loads(archive["meta"].item())
            indices, areas, baseline, future, cutoffs = (
                archive[key] for key in ("indices", "areas", "baseline", "future", "thresholds"))
    ids = list(sample.species_index)
    scenarios = meta["scenarios"]
    threshold_digest = meta.get("provenance", {}).get("thresholdTableSha256", sample.maxsss.thresholdTableSha256)
    if (meta["schema"] != 1 or meta["cityId"] != city_id or ids != meta["speciesIds"]
            or meta["releaseVersion"] != sample.data_version or len(scenarios) != 6
            or set(scenarios) != set(SCENARIOS) or threshold_digest != sample.maxsss.thresholdTableSha256
            or not np.array_equal(cutoffs, sample.thresholds)):
        raise ValueError("City inputs disagree with the verified 39-species release or its thresholds.")
    grid = Grid.model_validate(meta["grid"])
    overlap, y, x = verify_grid(grid, indices, sample, city_id)
    if (areas.shape != indices.shape or not (np.isfinite(areas) & (areas > 0)).all()
            or baseline.shape != (len(ids), indices.size) or future.shape != (6, *baseline.shape)
            or any(a.dtype != np.float32 or not ((a >= 0) & (a <= 1)).all() for a in (baseline, future))
            or areas.dtype != np.float64 or cutoffs.dtype != np.float64):
        raise ValueError("City predictions and areas must be complete on shared support and use the declared scale.")
    expected_support = entry.supportSha256 if entry else JIAXING_SUPPORT_SHA256
    verify_city_support(grid, indices, areas, expected_support)
    if entry and (len(indices) != entry.cellCount or not math.isclose(
            math.fsum(areas), entry.areaKm2, abs_tol=1e-7, rel_tol=0)):
        raise ValueError("City cell count or area disagrees with the manifest.")
    if not np.array_equal(baseline[:, overlap], sample.values[:, y, x]) or any(
        not np.array_equal(future[k][:, overlap], climate[key][0][:, y, x]) for k, key in enumerate(scenarios)
    ):
        raise ValueError("Shared native cells disagree with the verified baseline or future release.")
    version = f"city-area-v1-{digest[:16]}"
    area = AreaComparison(meta, grid, indices, areas, baseline.astype(np.float64), future.astype(np.float64),
                          cutoffs, version)
    if entry:
        verify_results(area, entry)
    return area, digest


def verify_results(area, entry):
    for (budget, holdout), run in area.portfolios.items():
        expected = entry.results[holdout][str(budget)]
        if expected.eligibleGainSpecies != run["eligibleGainSpecies"]:
            raise ValueError("City eligible species count disagrees with the manifest.")
        for strategy, metrics in expected.summary.items():
            actual = run["summary"][strategy]
            for key, value in metrics.model_dump().items():
                if value == actual[key]:
                    continue
                if value is None or actual[key] is None or not math.isclose(value, actual[key], abs_tol=1e-9, rel_tol=0):
                    raise ValueError("City portfolio summary disagrees with the manifest.")


def load_area_comparison(directory: Path, sample: Sample, climate: dict):
    """Open the legacy standalone Jiaxing package."""
    path = directory / f"area-{CITY_ID}.npz"
    if sample.dataset.release_id != FULL_RELEASE_ID or not path.is_file():
        return None
    check_release(sample, climate)
    return read_city(path, sample, climate)[0]


class CityCatalog:
    def __init__(self, directory, entries, sample, climate):
        self.sample, self.climate = sample, climate
        self.entries = {}
        self.paths = {}
        self.cache = OrderedDict()
        self.lock = Lock()
        self.outline_lock = Lock()
        self.outlines = None
        directory = directory.resolve(strict=True)
        for entry in entries:
            path = (directory / entry.file).resolve()
            if (PureWindowsPath(entry.file).anchor or not path.is_relative_to(directory)
                    or path.suffix.lower() != ".npz" or not path.is_file()):
                raise ValueError(f"Missing or invalid relative city package path for {entry.cityId}.")
            if entry.cityId in self.entries or path in self.paths.values():
                raise ValueError("The city index repeats a city ID or package path.")
            if entry.cityId == CITY_ID and entry.supportSha256 != JIAXING_SUPPORT_SHA256:
                raise ValueError("Jiaxing municipal support disagrees with the archived native coordinates.")
            self.entries[entry.cityId] = entry
            self.paths[entry.cityId] = path

    def overview(self, budget, holdout):
        return {"budget": budget, "holdout": holdout,
                "cities": [entry.overview(budget, holdout) for entry in self.entries.values()]}

    def footprint_geojson(self):
        # The geometry is common to every budget and pathway. Read only the support arrays.
        with self.outline_lock:
            if self.outlines is None:
                features = []
                for city_id, entry in self.entries.items():
                    with np.load(self.paths[city_id], allow_pickle=False) as archive:
                        meta = json.loads(archive["meta"].item())
                        grid = Grid.model_validate(meta["grid"])
                        indices, areas = archive["indices"], archive["areas"]
                    if (meta["cityId"] != city_id or meta["releaseVersion"] != self.sample.data_version
                            or indices.size != entry.cellCount or areas.shape != indices.shape):
                        raise ValueError("Footprint does not match its city entry.")
                    verify_grid(grid, indices, self.sample, city_id)
                    verify_city_support(grid, indices, areas, entry.supportSha256)
                    mask = np.zeros((grid.height, grid.width), dtype="uint8")
                    mask.flat[indices] = 1
                    transform = from_bounds(*grid.bounds, grid.width, grid.height)
                    polygons = []
                    for polygon, _ in shapes(mask, mask=mask.astype(bool), transform=transform):
                        polygons.append([[[round(x, 6), round(y, 6)] for x, y in ring]
                                         for ring in polygon["coordinates"]])
                    features.append({"type": "Feature", "properties": {"cityId": city_id},
                                     "geometry": {"type": "MultiPolygon", "coordinates": polygons}})
                self.outlines = json.dumps({"type": "FeatureCollection", "features": features},
                                           separators=(",", ":"), allow_nan=False).encode()
            return self.outlines

    def get(self, city_id):
        with self.lock:
            if city_id in self.cache:
                self.cache.move_to_end(city_id)
            else:
                area, _ = read_city(self.paths[city_id], self.sample, self.climate, self.entries[city_id])
                self.cache[city_id] = area
                if len(self.cache) > 2:
                    self.cache.popitem(last=False)
            return self.cache[city_id]


def load_area_catalog(directory: Path, sample: Sample, climate: dict, area_directory: Path | None = None):
    if sample.dataset.release_id != FULL_RELEASE_ID:
        return None
    root = area_directory if area_directory is not None else directory
    manifest = root / "area-cities.json"
    if manifest.is_file():
        check_release(sample, climate)
        index = CityIndex.model_validate_json(manifest.read_bytes())
        if (index.releaseVersion != sample.data_version
                or index.thresholdTableSha256 != sample.maxsss.thresholdTableSha256):
            raise ValueError("The city index belongs to a different release or threshold table.")
        return CityCatalog(root, index.cities, sample, climate)
    if area_directory is not None:
        raise ValueError(f"INVAPLAN_AREA_DIR requires a city index at {manifest}.")
    path = directory / f"area-{CITY_ID}.npz"
    if not path.is_file():
        return None
    check_release(sample, climate)
    area, digest = read_city(path, sample, climate)
    run = area.comparison(10, "ssp585")
    results = {holdout: {str(b): {key: area.comparison(b, holdout)[key] for key in ("summary", "eligibleGainSpecies")}
                         for b in BUDGETS} for holdout in HOLDOUTS}
    entry = CityEntry(cityId=CITY_ID, nameZh="嘉兴市", nameEn="Jiaxing", cellCount=len(run["indices"]),
                      areaKm2=math.fsum(run["areas"]), file=path.name, sha256=digest,
                      supportSha256=JIAXING_SUPPORT_SHA256, results=results)
    catalog = CityCatalog(directory, [entry], sample, climate)
    catalog.cache[CITY_ID] = area
    return catalog


def active_area(request, city_id):
    catalog = request.app.state.area_comparison
    if catalog is None or city_id not in catalog.entries:
        raise HTTPException(404, "area_comparison_unavailable")
    try:
        return catalog.get(city_id)
    except (OSError, ValueError, KeyError, BadZipFile):
        logger.exception("Unable to verify the city comparison package for %s", city_id)
        raise HTTPException(503, "area_comparison_invalid") from None


def check_budget(budget):
    if budget not in BUDGETS:
        raise HTTPException(422, "invalid_area_budget")


@router.get("")
def overview(request: Request, budget: int = 10, holdout: Holdout = "ssp585"):
    check_budget(budget)
    catalog = request.app.state.area_comparison
    if catalog is None:
        raise HTTPException(404, "area_comparison_unavailable")
    return catalog.overview(budget, holdout)


@router.get("/footprints")
def city_footprints(request: Request):
    catalog = request.app.state.area_comparison
    if catalog is None:
        raise HTTPException(404, "area_comparison_unavailable")
    try:
        return Response(catalog.footprint_geojson(), media_type="application/geo+json")
    except (OSError, ValueError, KeyError, BadZipFile):
        logger.exception("Unable to verify municipal footprints")
        raise HTTPException(503, "area_comparison_invalid") from None


@router.get("/{city_id}")
def compare(city_id: str, request: Request, budget: int = 10, holdout: Holdout = "ssp585"):
    check_budget(budget)
    area = active_area(request, city_id)
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
