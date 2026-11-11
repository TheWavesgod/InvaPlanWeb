"""Import and verify versioned, locally stored baseline datasets."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
import rasterio
from rasterio.windows import Window, bounds, from_bounds


DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "datasets" / "jiaxing-sample.json"


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Names(ManifestModel):
    zh: str = Field(min_length=1)
    en: str = Field(min_length=1)


class Region(ManifestModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    name: Names
    bounds: tuple[float, float, float, float]

    @model_validator(mode="after")
    def check_extent(self):
        west, south, east, north = self.bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("Region bounds must be ordered WGS 84 longitude and latitude.")
        return self


class Species(ManifestModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    name: Names
    latin: str = Field(min_length=1)
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.[Tt][Ii][Ff][Ff]?$")
    scale: float = Field(gt=0)
    offset: float
    exploratory: bool = False


class Dataset(ManifestModel):
    schema_version: Literal[1]
    release_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    region: Region
    source: Names
    baseline_period: str | None
    species: list[Species] = Field(min_length=1)
    default_species_ids: list[str] = Field(min_length=1)
    default_layer_id: str

    @model_validator(mode="after")
    def check_species(self):
        ids = {species.id for species in self.species}
        files = {species.file.casefold() for species in self.species}
        defaults = set(self.default_species_ids)
        if len(ids) != len(self.species) or len(files) != len(self.species):
            raise ValueError("Species IDs and TIFF filenames must be unique.")
        if len(defaults) != len(self.default_species_ids) or not defaults <= ids or self.default_layer_id not in ids:
            raise ValueError("Default species and layer must refer to listed species.")
        return self


class Grid(ManifestModel):
    bounds: tuple[float, float, float, float]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    row0: int = Field(ge=0)
    col0: int = Field(ge=0)

    @model_validator(mode="after")
    def check_extent(self):
        west, south, east, north = self.bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("Grid bounds must be ordered WGS 84 longitude and latitude.")
        return self


class Release(ManifestModel):
    schema_version: Literal[1]
    dataset: Dataset
    grid: Grid
    data_version: str
    values_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: dict[str, str]

    @model_validator(mode="after")
    def check_sources(self):
        ids = {species.id for species in self.dataset.species}
        if self.source_sha256.keys() != ids or any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in self.source_sha256.values()
        ):
            raise ValueError("Source checksums must cover every species.")
        return self


def read_dataset(manifest: Path) -> Dataset:
    return Dataset.model_validate_json(manifest.read_bytes())


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()


def data_version(dataset: Dataset, grid: Grid, values: np.ndarray) -> str:
    # Preserve the existing version for the same sample values and geometry.
    species = tuple((item.id, item.name.zh, item.latin) for item in dataset.species)
    window = (grid.col0, grid.row0, grid.width, grid.height)
    digest = sha256(repr((species, grid.bounds, window)).encode())
    digest.update(values.astype("<f4", copy=False).tobytes())
    return "sample-window-" + digest.hexdigest()[:12]


def validate_values(dataset: Dataset, grid: Grid, values: np.ndarray):
    if values.shape != (len(dataset.species), grid.height, grid.width) or values.dtype.kind != "f" or values.dtype.itemsize != 4:
        raise ValueError("The value array does not match the declared species and grid.")
    if np.isinf(values).any():
        raise ValueError("Infinite values are invalid. Use NaN for missing predictions.")
    for item, array in zip(dataset.species, values, strict=True):
        valid = array[np.isfinite(array)]
        if valid.size == 0 or valid.min() < 0 or valid.max() > 1:
            raise ValueError(f"{item.id} has no valid values or is outside the declared 0–1 scale.")


def read_rasters(dataset: Dataset, directory: Path) -> tuple[Grid, np.ndarray]:
    directory = directory.resolve(strict=True)
    reference = None
    parts = []
    for item in dataset.species:
        path = (directory / item.file).resolve(strict=True)
        if path.parent != directory or not path.is_file():
            raise ValueError(f"TIFF must be inside the input directory: {item.file}")
        with rasterio.open(path, "r") as source:
            transform = source.transform
            if (source.crs is None or source.crs.to_epsg() != 4326 or source.count != 1 or
                transform.b or transform.d or transform.a <= 0 or transform.e >= 0):
                raise ValueError(f"{item.file} must be a single-band, north-up WGS 84 raster.")
            encoding = (source.scales[0], source.offsets[0])
            if encoding not in ((1, 0), (item.scale, item.offset)):
                raise ValueError(f"{item.file} scale or offset disagrees with the manifest.")
            grid_signature = (source.crs, transform, source.width, source.height)
            if reference is None:
                reference = grid_signature
                window = from_bounds(*dataset.region.bounds, transform=transform)
                window = Window(*(round(value) for value in window.flatten()))
                col, row, width, height = window.flatten()
                if (min(col, row) < 0 or min(width, height) < 1 or
                    col + width > source.width or row + height > source.height):
                    raise ValueError("The requested window is empty or outside the source grid.")
                grid = Grid(bounds=bounds(window, transform), width=width, height=height, row0=row, col0=col)
            elif grid_signature != reference:
                raise ValueError(f"{item.file} does not align with the first raster.")
            array = source.read(1, window=window, masked=True).astype(np.float32).filled(np.nan)
            array[~np.isfinite(array)] = np.nan
            if (item.scale, item.offset) != (1, 0):
                array = (array.astype(np.float64) * item.scale + item.offset).astype(np.float32)
            parts.append(array)

    values = np.stack(parts)
    validate_values(dataset, grid, values)
    return grid, values


def import_dataset(manifest: Path, source: Path, destination: Path) -> Release:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Release output already exists: {destination}")
    dataset = read_dataset(manifest)
    grid, values = read_rasters(dataset, source)
    checksums = {item.id: file_sha256(source / item.file) for item in dataset.species}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".invaplan-import-", dir=destination.parent) as stage:
        prepared = Path(stage) / "release"
        prepared.mkdir()
        value_path = prepared / "values.npz"
        np.savez_compressed(value_path, values=values)
        release = Release(
            schema_version=1, dataset=dataset, grid=grid,
            data_version=data_version(dataset, grid, values),
            values_sha256=file_sha256(value_path), source_sha256=checksums,
        )
        (prepared / "manifest.json").write_text(release.model_dump_json(indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise FileExistsError(f"Release output already exists: {destination}")
        prepared.rename(destination)
    return release


def read_release(directory: Path) -> tuple[Release, np.ndarray]:
    release = Release.model_validate_json((directory / "manifest.json").read_bytes())
    value_path = directory / "values.npz"
    if file_sha256(value_path) != release.values_sha256:
        raise ValueError("values.npz checksum does not match the release manifest.")
    with np.load(value_path, allow_pickle=False) as archive:
        values = archive["values"]
    validate_values(release.dataset, release.grid, values)
    if data_version(release.dataset, release.grid, values) != release.data_version:
        raise ValueError("The calculated data version does not match the release manifest.")
    return release, values
