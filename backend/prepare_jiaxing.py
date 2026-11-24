"""Prepare the 39-species Jiaxing release from the selected Chapter 4 products."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import numpy as np

from .climate import SCENARIOS
from .datasets import DEFAULT_MANIFEST, Release, Species, data_version, file_sha256, read_dataset, read_rasters
from .sample import FULL_RELEASE_ID, MaxSSSProfile, cell_id


def rows(path, delimiter=","):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def prepare(archive: Path, city: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    selection = archive / "manifests/SPECIES_PREDICTIONS_20261120.tsv"
    threshold_table = city / "tables/species_threshold_maxsss_accepted_with_member_recovery.csv"
    records = rows(threshold_table)
    ids = sorted(r["tid"] for r in records)
    if ids != [f"PROD{i:04}" for i in range(1, 40)]:
        raise ValueError("Expected exactly one threshold record for each of the 39 study species.")
    cutoffs = {r["tid"]: float(r["threshold"]) for r in records}
    template = read_dataset(DEFAULT_MANIFEST)
    known_names = {s.id: s.name.zh for s in template.species}
    name_file = DEFAULT_MANIFEST.with_name("jiaxing-species-names.json")
    if name_file.is_file():
        known_names.update(json.loads(name_file.read_bytes()))
    if not set(known_names) <= set(ids):
        raise ValueError("The bilingual species catalogue does not match the prediction selection.")
    species = {}
    for r in sorted(records, key=lambda row: row["tid"]):
        tid, latin = r["tid"], r["species"]
        if not r["status"].endswith("_QC_PASS") or not 0 <= cutoffs[tid] <= 1:
            raise ValueError(f"Unaccepted threshold for {tid}")
        species[tid] = Species(id=tid, latin=latin, name={"zh": known_names.get(tid, latin), "en": latin},
                               file=f"{tid}.tif", scale=1, offset=0, exploratory=r["exploratory"] == "TRUE")
    dataset = type(template).model_validate({
        **template.model_dump(), "release_id": FULL_RELEASE_ID, "species": list(species.values()),
        "default_species_ids": ids,
        "source": {"zh": "第4章嘉兴窗口 · 39种入侵植物 S2 基准适宜度",
                   "en": "Chapter 4 Jiaxing window · S2 baseline suitability for 39 invasive plants"},
    })

    products = {}
    for row in rows(selection, "\t"):
        kind = row["kind"]
        if kind == "current_S2_baseline_referenced_by_future_maps":
            key = "baseline"
        elif kind == "future_GCM_consensus_mean" and row["gcm"] == "mean":
            key = f'{row["ssp"]}-{row["period"]}'
        else:
            continue
        tid = row["tid"]
        if row["species"] != species[tid].latin or (key, tid) in products or not row["independent_qc"]:
            raise ValueError(f"Invalid prediction entry for {key}/{tid}")
        products[key, tid] = (archive / row["relative_path"], row["sha256"])
    if set(products) != {(key, tid) for key in ["baseline", *SCENARIOS] for tid in ids}:
        raise ValueError("Prediction selection is incomplete.")

    for name in ("species_binary_current", "species_binary_current_recovered", "species_binary_current_member_recovered"):
        for r in rows(city / "manifests" / f"{name}.csv"):
            tid = r["tid"]
            path = city / "bundles/current" / Path(r["binary_path"]).name
            if (r["current_sha"] != products["baseline", tid][1] or
                    abs(float(r["cutoff"]) - cutoffs[tid]) > 1e-12 or ("binary", tid) in products):
                raise ValueError(f"Binary input lineage mismatch for {tid}")
            products["binary", tid] = (path, r["binary_sha"])
    if any(("binary", tid) not in products for tid in ids):
        raise ValueError("Missing archived binary map.")

    def read(key):
        family, tid = key
        path, expected = products[key]
        path = path.resolve(strict=True)
        if file_sha256(path) != expected:
            raise ValueError(f"Source checksum mismatch for {path}")
        member = species[tid].model_copy(update={"file": path.name})
        grid, array = read_rasters(dataset.model_copy(update={"species": [member]}), path.parent)
        return family, tid, grid, array[0]

    blocks = {}
    grids = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for family, tid, grid, array in pool.map(read, products):
            blocks[family, tid] = array
            grids[family, tid] = grid
    grid = grids["baseline", ids[0]]
    for (family, tid), other in grids.items():
        # Binary maps have a clipped source extent. Compare the physical window
        # with sub-millimetre tolerance for affine floating-point roundoff.
        if ((other.width, other.height) != (grid.width, grid.height)
                or not np.allclose(other.bounds, grid.bounds, rtol=0, atol=1e-9)):
            raise ValueError(f"Window geometry mismatch for {family}/{tid}")
        if family != "binary" and (other.row0, other.col0) != (grid.row0, grid.col0):
            raise ValueError(f"Native grid mismatch for {family}/{tid}")
    def stack(key):
        return np.stack([blocks[key, tid] for tid in ids])

    baseline = stack("baseline")
    binary = stack("binary")
    valid, archived = np.isfinite(baseline), np.isfinite(binary)
    predicted = baseline.astype(np.float64) >= np.array([cutoffs[i] for i in ids])[:, None, None]
    if (not (valid == valid[0]).all() or not (archived == archived[0]).all()
            or (archived & ~valid).any() or (predicted[archived] != binary[archived]).any()):
        raise ValueError("Per-species masks or threshold classifications disagree with the binary archive.")
    version = data_version(dataset, grid, baseline)
    profile = MaxSSSProfile(
        dataVersion=version, thresholdTable=threshold_table.name, thresholdTableSha256=file_sha256(threshold_table),
        thresholds=cutoffs, archiveCellCount=int(archived[0].sum()),
        demoOnlyCells=[cell_id(grid.row0 + int(y), grid.col0 + int(x))
                       for y, x in zip(*np.where(valid[0] & ~archived[0]), strict=True)],
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp) / "release"
        stage.mkdir()
        np.savez_compressed(stage / "values.npz", values=baseline)
        release = Release(schema_version=1, dataset=dataset, grid=grid, data_version=version,
                          values_sha256=file_sha256(stage / "values.npz"),
                          source_sha256={tid: products["baseline", tid][1] for tid in ids})
        (stage / "manifest.json").write_text(release.model_dump_json(indent=2), encoding="utf-8")
        (stage / "maxsss.json").write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        np.savez_compressed(stage / "climate.npz", **{key: stack(key) for key in SCENARIOS})
        provenance = {
            "dataVersion": version, "speciesIds": ids, "climateSha256": file_sha256(stage / "climate.npz"),
            "profileSha256": file_sha256(stage / "maxsss.json"),
            "selection": selection.name, "selectionSha256": file_sha256(selection),
            "sources": {family: {tid: {"file": products[family, tid][0].name, "sha256": products[family, tid][1]}
                                 for tid in ids} for family in ["baseline", "binary", *SCENARIOS]},
        }
        (stage / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        # OneDrive can hold directory handles on Windows. Publish the small
        # files into a new directory and make its entry manifest visible last.
        destination.mkdir()
        try:
            for name in ("values.npz", "maxsss.json", "climate.npz", "provenance.json"):
                shutil.copyfile(stage / name, destination / name)
            pending = destination / "manifest.pending"
            shutil.copyfile(stage / "manifest.json", pending)
            pending.replace(destination / "manifest.json")
        except OSError:
            for name in ("values.npz", "maxsss.json", "climate.npz", "provenance.json", "manifest.pending"):
                (destination / name).unlink(missing_ok=True)
            destination.rmdir()
            raise
    print(f"{destination}: {version}, {len(ids)} species, {profile.archiveCellCount} archive cells, "
          f"{len(profile.demoOnlyCells)} additional cells, {len(SCENARIOS)} scenarios.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("archive", "city", "destination"):
        parser.add_argument(argument, type=Path)
    args = parser.parse_args()
    prepare(args.archive, args.city, args.destination)
