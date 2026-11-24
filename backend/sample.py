"""Serve analysis-ready baseline data on its original grid."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import zlib

import numpy as np
from pydantic import BaseModel, Field

from .datasets import DEFAULT_MANIFEST, Dataset, Grid, data_version, file_sha256, read_dataset, read_rasters, read_release


MAX_K = 30
JIAXING_S2_VERSION = "sample-window-7f51acf6589d"
FULL_RELEASE_ID = "jiaxing-39-s2-v1"


class MaxSSSProfile(BaseModel):
    dataVersion: str
    thresholdTable: str
    thresholdTableSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    thresholds: dict[str, float]
    archiveCellCount: int = Field(ge=0)
    demoOnlyCells: list[str]


def _raster_png(array: np.ndarray, *, signed: bool = False) -> bytes:
    """Encode the small RGB display raster with no interpolation or georeferencing."""
    valid = np.isfinite(array)
    values = np.where(valid, array, 0)[..., None]
    high = np.array([144, 24, 50])
    if signed:
        low = np.array([247, 247, 247])
        high = np.where(values < 0, [33, 86, 155], high)
        values = np.clip(np.abs(values) / 0.25, 0, 1)
    else:
        low = np.array([255, 239, 218])
    rgb = np.rint(low + (high - low) * values).astype(np.uint8)
    rgb[~valid] = [130, 130, 130]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))

    height, width = rgb.shape[:2]
    raw = b"".join(b"\x00" + row.tobytes() for row in rgb)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def cell_id(row: int, col: int) -> str:
    return f"r{row:05d}c{col:05d}"


@dataclass(frozen=True)
class Sample:
    dataset: Dataset
    species_index: dict[str, int]
    values: np.ndarray  # species x row x col
    thresholds: np.ndarray
    valid_counts: np.ndarray
    bounds: tuple[float, float, float, float]
    row0: int
    col0: int
    data_version: str
    layers: dict[str, bytes]
    index_by_id: dict[str, int]
    policy_version: str
    maxsss: MaxSSSProfile | None
    demo_only: frozenset[str]

    @property
    def width(self):
        return self.values.shape[2]

    @property
    def height(self):
        return self.values.shape[1]

    def coordinates(self, index: int) -> dict:
        y, x = divmod(index, self.width)
        row, col = self.row0 + y, self.col0 + x
        west, south, east, north = self.bounds
        key = cell_id(row, col)
        result = {
            "id": key,
            "row": row, "col": col,
            "longitude": west + (x + 0.5) * (east - west) / self.width,
            "latitude": north - (y + 0.5) * (north - south) / self.height,
        }
        if self.maxsss:
            result["binarySource"] = (
                "demo_derived" if key in self.demo_only else
                "archived" if np.isfinite(self.values[0, y, x]) else "no_data"
            )
        return result

    def catalog(self) -> dict:
        return {
            "name": self.dataset.region.name.zh,
            "nameEn": self.dataset.region.name.en,
            "regionId": self.dataset.region.id,
            "releaseId": self.dataset.release_id,
            "source": self.dataset.source.model_dump(),
            "dataVersion": self.data_version,
            "policyVersion": self.policy_version,
            "binaryCoverage": {
                "thresholdTable": self.maxsss.thresholdTable,
                "thresholdTableSha256": self.maxsss.thresholdTableSha256,
                "archiveCellCount": self.maxsss.archiveCellCount,
                "demoOnlyIndices": sorted(self.index_by_id[key] for key in self.demo_only),
            } if self.maxsss else None,
            "baselinePeriod": self.dataset.baseline_period,
            "defaultSpeciesIds": self.dataset.default_species_ids,
            "defaultLayerId": self.dataset.default_layer_id,
            "species": [
                {"id": item.id, "name": item.name.zh, "nameEn": item.name.en,
                 "latin": item.latin, "exploratory": item.exploratory}
                for item in self.dataset.species
            ],
            "grid": {
                "bounds": self.bounds, "width": self.width, "height": self.height,
                "row0": self.row0, "col0": self.col0,
            },
            "thresholds": {
                item.id: {"value": float(self.thresholds[i]), "validCount": int(self.valid_counts[i])}
                for i, item in enumerate(self.dataset.species)
            },
            "maxK": MAX_K,
        }


def prepare_sample(dataset: Dataset, grid: Grid, values: np.ndarray, version: str,
                   profile: MaxSSSProfile | None = None) -> Sample:
    valid_counts = np.isfinite(values).sum(axis=(1, 2))
    index_by_id = {
        cell_id(grid.row0 + y, grid.col0 + x): y * grid.width + x
        for y in range(grid.height) for x in range(grid.width)
    }
    demo_only = frozenset()
    if version == JIAXING_S2_VERSION:
        path = DEFAULT_MANIFEST.with_name("jiaxing-maxsss.json")
        if path.is_file():
            profile = MaxSSSProfile.model_validate_json(path.read_bytes())
    if dataset.release_id == FULL_RELEASE_ID and profile is None:
        raise ValueError("The 39-species release requires its verified maxSSS profile.")
    if profile is not None:
        ids = [s.id for s in dataset.species]
        if profile.dataVersion != version or set(profile.thresholds) != set(ids):
            raise ValueError("The maxSSS profile does not match the active S2 sample.")
        thresholds = np.array([profile.thresholds[code] for code in ids], dtype=np.float64)
        valid = np.isfinite(values)
        if not ((thresholds >= 0) & (thresholds <= 1)).all() or not (valid == valid[0]).all():
            raise ValueError("Invalid maxSSS cutoffs or inconsistent species masks.")
        demo_only = frozenset(profile.demoOnlyCells)
        valid_ids = {key for key, i in index_by_id.items() if valid[0].flat[i]}
        if (len(demo_only) != len(profile.demoOnlyCells) or not demo_only <= valid_ids
                or len(valid_ids) - len(demo_only) != profile.archiveCellCount):
            raise ValueError("The binary coverage does not match the S2 grid.")
        digest = sha256(profile.model_dump_json().encode()).hexdigest()[:12]
        policy_version = f"s2_oof_maxsss_v1_{digest}"
    else:
        thresholds = np.array([
            np.quantile(array[np.isfinite(array)].astype("float64"), 0.9, method="linear")
            for array in values
        ])
        policy_version = "pilot_baseline_p90_v1"
    return Sample(
        dataset=dataset,
        species_index={item.id: i for i, item in enumerate(dataset.species)},
        values=values, thresholds=thresholds, valid_counts=valid_counts,
        bounds=grid.bounds, row0=grid.row0, col0=grid.col0, data_version=version,
        layers={item.id: _raster_png(array) for item, array in zip(dataset.species, values, strict=True)},
        index_by_id=index_by_id,
        policy_version=policy_version, maxsss=profile, demo_only=demo_only,
    )


def load_sample(directory: Path, manifest: Path = DEFAULT_MANIFEST) -> Sample:
    dataset = read_dataset(manifest)
    grid, values = read_rasters(dataset, directory)
    return prepare_sample(dataset, grid, values, data_version(dataset, grid, values))


def load_prepared_sample(directory: Path) -> Sample:
    release, values = read_release(directory)
    profile = None
    if release.dataset.release_id == FULL_RELEASE_ID:
        meta = json.loads((directory / "provenance.json").read_bytes())
        path = directory / "maxsss.json"
        if file_sha256(path) != meta["profileSha256"]:
            raise ValueError("The threshold profile checksum disagrees with the release.")
        profile = MaxSSSProfile.model_validate_json(path.read_bytes())
    return prepare_sample(release.dataset, release.grid, values, release.data_version, profile)
