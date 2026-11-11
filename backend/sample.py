"""Serve analysis-ready baseline data on its original grid."""

from dataclasses import dataclass
from pathlib import Path
import struct
import zlib

import numpy as np

from .datasets import DEFAULT_MANIFEST, Dataset, Grid, data_version, read_dataset, read_rasters, read_release


POLICY_VERSION = "pilot_baseline_p90_v1"
MAX_K = 30


def _raster_png(array: np.ndarray) -> bytes:
    """Encode the small RGB display raster with no interpolation or georeferencing."""
    valid = np.isfinite(array)
    t = np.where(valid, array, 0)[..., None]
    low = np.array([234, 239, 215])
    high = np.array([35, 126, 97])
    rgb = np.rint(low + (high - low) * t).astype(np.uint8)
    rgb[~valid] = [124, 136, 130]

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
        return {
            "id": cell_id(row, col),
            "row": row, "col": col,
            "longitude": west + (x + 0.5) * (east - west) / self.width,
            "latitude": north - (y + 0.5) * (north - south) / self.height,
        }

    def catalog(self) -> dict:
        return {
            "name": self.dataset.region.name.zh,
            "nameEn": self.dataset.region.name.en,
            "regionId": self.dataset.region.id,
            "releaseId": self.dataset.release_id,
            "source": self.dataset.source.model_dump(),
            "dataVersion": self.data_version,
            "policyVersion": POLICY_VERSION,
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


def prepare_sample(dataset: Dataset, grid: Grid, values: np.ndarray, version: str) -> Sample:
    valid_counts = np.isfinite(values).sum(axis=(1, 2))
    thresholds = np.array([
        np.quantile(array[np.isfinite(array)].astype("float64"), 0.9, method="linear")
        for array in values
    ])
    index_by_id = {
        cell_id(grid.row0 + y, grid.col0 + x): y * grid.width + x
        for y in range(grid.height) for x in range(grid.width)
    }
    return Sample(
        dataset=dataset,
        species_index={item.id: i for i, item in enumerate(dataset.species)},
        values=values, thresholds=thresholds, valid_counts=valid_counts,
        bounds=grid.bounds, row0=grid.row0, col0=grid.col0, data_version=version,
        layers={item.id: _raster_png(array) for item, array in zip(dataset.species, values, strict=True)},
        index_by_id=index_by_id,
    )


def load_sample(directory: Path, manifest: Path = DEFAULT_MANIFEST) -> Sample:
    dataset = read_dataset(manifest)
    grid, values = read_rasters(dataset, directory)
    return prepare_sample(dataset, grid, values, data_version(dataset, grid, values))


def load_prepared_sample(directory: Path) -> Sample:
    release, values = read_release(directory)
    return prepare_sample(release.dataset, release.grid, values, release.data_version)
