"""Load the corrected Chapter 4 projections on the baseline sample grid."""

from csv import DictReader
import json
from pathlib import Path

import numpy as np

from .datasets import data_version, file_sha256, read_rasters, validate_values, Grid
from .sample import Sample, _raster_png


SCENARIOS = {
    f"{ssp}-{period}": {"id": f"{ssp}-{period}", "ssp": label, "period": period}
    for ssp, label in (("ssp126", "SSP1-2.6"), ("ssp245", "SSP2-4.5"), ("ssp585", "SSP5-8.5"))
    for period in ("2041-2060", "2081-2100")
}


def render_scenario(sample: Sample, future: np.ndarray):
    change = future - sample.values
    return future, {
        (kind, species.id): _raster_png(values[i], signed=kind == "change")
        for kind, values in (("future", future), ("change", change))
        for i, species in enumerate(sample.dataset.species)
    }


def load_prepared_climate(directory: Path, sample: Sample) -> dict:
    meta = json.loads((directory / "provenance.json").read_bytes())
    arrays = directory / "climate.npz"
    if (meta["dataVersion"] != sample.data_version or meta["speciesIds"] != list(sample.species_index)
            or file_sha256(arrays) != meta["climateSha256"]):
        raise ValueError("Climate layers do not match the release manifest.")
    grid = Grid(bounds=sample.bounds, row0=sample.row0, col0=sample.col0, width=sample.width, height=sample.height)
    with np.load(arrays, allow_pickle=False) as archive:
        if set(archive.files) != set(SCENARIOS):
            raise ValueError("Expected all six climate scenarios in the prepared release.")
        scenarios = {}
        for key in SCENARIOS:
            future = archive[key]
            validate_values(sample.dataset, grid, future)
            scenarios[key] = render_scenario(sample, future)
        return scenarios


def load_climate(root: Path, sample: Sample) -> dict:
    """Read windowed float rasters from the local scientific release."""
    with (root / "manifests" / "SCIENTIFIC_PRODUCTS.tsv").open(encoding="utf-8-sig", newline="") as stream:
        products = {row["product_id"]: row for row in DictReader(stream, delimiter="\t")}

    def read_family(relative_dir: str, product_id):
        directories = set()
        for species in sample.dataset.species:
            filename = f"{species.id}.tif"
            row = products.get(product_id(species.id))
            if (not row or row["verification_status"] != "PASS" or
                    row["publication_relative_path"] != f"{relative_dir}/{filename}"):
                raise ValueError(f"Missing accepted climate release entry: {relative_dir}/{filename}")
            path = (root / relative_dir / filename).resolve(strict=True)
            if path.name != filename or file_sha256(path) != row["archive_sha256"]:
                raise ValueError(f"Climate release checksum mismatch: {relative_dir}/{filename}")

            directories.add(path.parent)
        if len(directories) != 1:
            raise ValueError(f"Climate rasters resolve to separate source directories: {relative_dir}")

        dataset = sample.dataset.model_copy(update={
            "species": [
                species.model_copy(update={"file": f"{species.id}.tif", "scale": 1.0, "offset": 0.0})
                for species in sample.dataset.species
            ],
        })
        return read_rasters(dataset, next(iter(directories)))

    grid, baseline = read_family("current/per_species_39", lambda species: f"CURRENT_S2_BASELINE|{species}")
    if data_version(sample.dataset, grid, baseline) != sample.data_version:
        raise ValueError("The active baseline does not match the climate release.")

    scenarios = {}
    for key, scenario in SCENARIOS.items():
        ssp = key.split("-", 1)[0]
        period = scenario["period"]
        future_grid, future = read_family(
            f"future/GCM_consensus_234/{ssp}/{period}",
            lambda species: f"AGG|future_GCM_consensus_mean|{species}|{ssp}|{period}|mean",
        )
        if future_grid != grid:
            raise ValueError(f"Climate grid does not match the baseline for {key}.")
        scenarios[key] = render_scenario(sample, future)
    return scenarios
