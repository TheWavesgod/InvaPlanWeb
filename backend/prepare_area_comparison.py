"""Build the Jiaxing city comparison inputs from accepted native-grid predictions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import rasterio
from rasterio.windows import Window, bounds, from_bounds

from .climate import SCENARIOS
from .datasets import Grid, file_sha256, read_release
from .prepare_jiaxing import rows
from .sample import FULL_RELEASE_ID, MaxSSSProfile


CITY_ID = "330400"
ZONE_ENVELOPE = (119.9, 30.12, 121.65, 31.3)
ARCHIVE = Path("K:/C4_SSP_ARCHIVE/SSP_FINAL_RELEASE_CORRECTED_20261012")
CITY = Path("K:/C4_PUBLICATION_MASTER/outputs/redistribution/city")
RELEASE = Path(__file__).resolve().parents[1] / "data/jiaxing-39"
SELECTION = "SPECIES_PREDICTIONS_20261120.tsv"


def check_grid(source):
    affine = source.transform
    if (source.count != 1 or source.crs is None or source.crs.to_epsg() != 4326
            or affine.b != 0 or affine.d != 0
            or not np.allclose([affine.a, affine.e], [1 / 120, -1 / 120], atol=1e-12, rtol=0)):
        raise ValueError(f"Expected a single north-up 30 arcsecond WGS84 layer: {source.name}")


def aligned_window(source, extent):
    raw = from_bounds(*extent, transform=source.transform)
    window = Window(*(round(value) for value in raw.flatten()))
    if (not np.allclose(bounds(window, source.transform), extent, rtol=0, atol=1e-9)
            or window.col_off < 0 or window.row_off < 0 or window.col_off + window.width > source.width
            or window.row_off + window.height > source.height):
        raise ValueError(f"Native cells do not align with the requested window: {source.name}")
    return window


def cell_areas(grid, indices):
    """Integrate the WGS84 ellipsoid surface between parallels and meridians."""
    west, south, east, north = grid.bounds
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    sin_lat = np.sin(np.deg2rad(np.linspace(north, south, grid.height + 1)))
    integral = (sin_lat / (1 - e2 * sin_lat**2) + np.arctanh(np.sqrt(e2) * sin_lat) / np.sqrt(e2)) / 2
    dlon = np.deg2rad((east - west) / grid.width)
    row_areas = -np.diff(integral) * a**2 * (1 - e2) * dlon / 1e6
    return row_areas[indices // grid.width]


def city_geometry(city, reference, release_grid):
    support = city / "support"
    coverage_path = support / "city_coverage.csv"
    zone_path = support / "city_zone_common.tif"
    coverage = sorted(rows(coverage_path), key=lambda row: int(row["adcode"]))
    codes = [row["adcode"] for row in coverage]
    if len(set(codes)) != len(codes) or len(codes) != 62:
        raise ValueError("Expected the archived 62-city coverage table.")
    zone = codes.index(CITY_ID) + 1
    record = coverage[zone - 1]
    if zone != 18 or record["name_en"] != "Jiaxing":
        raise ValueError("The city-zone lookup does not identify Jiaxing.")
    with rasterio.open(zone_path) as src:
        check_grid(src)
        tentative = from_bounds(*ZONE_ENVELOPE, transform=src.transform)
        envelope = Window(*(round(value) for value in tentative.flatten()))
        mask = (src.read(1, window=envelope, masked=True) == zone).filled(False)
        yy, xx = np.nonzero(mask)
        if not len(yy) or len(yy) != int(record["valid_cells"]):
            raise ValueError("Published city support count disagrees with the zone raster.")
        y, x = int(yy.min()), int(xx.min())
        height, width = int(yy.max()) - y + 1, int(xx.max()) - x + 1
        tight = Window(envelope.col_off + x, envelope.row_off + y, width, height)
        extent = bounds(tight, src.transform)
        indices = np.flatnonzero(mask[y:y + height, x:x + width]).astype(np.int32)
    with rasterio.open(reference) as src:
        check_grid(src)
        source_window = aligned_window(src, extent)
        demo = aligned_window(src, release_grid.bounds)
        if (demo.col_off, demo.row_off) != (release_grid.col0, release_grid.row0):
            raise ValueError("Baseline absolute pixel IDs disagree with the existing release.")
        grid = Grid(bounds=bounds(source_window, src.transform), width=width, height=height,
                    row0=source_window.row_off, col0=source_window.col_off)
    areas = cell_areas(grid, indices)
    expected_area = float(record["valid_area_km2"])
    if (not np.isfinite(areas).all() or np.any(areas <= 0) or not np.isfinite(expected_area)
            or abs(areas.sum() - expected_area) > 0.01):
        raise ValueError("Cell areas disagree with the published Jiaxing common support area.")
    info = {
        "zoneFile": zone_path.name, "zoneSha256": file_sha256(zone_path), "zoneId": zone,
        "coverageFile": coverage_path.name, "coverageSha256": file_sha256(coverage_path),
        "expectedCells": len(indices), "publishedAreaKm2": expected_area,
        "areaMethod": "WGS84 ellipsoid surface bounded by native parallels and meridians",
    }
    return grid, indices, areas, info


def selected_products(archive, selection, release, provenance):
    species = {item.id: item for item in release.dataset.species}
    products = {}
    for row in rows(selection, "\t"):
        tid = row["tid"]
        if row["kind"] == "current_S2_baseline_referenced_by_future_maps":
            family = "baseline"
            identity = f"CURRENT_S2_BASELINE|{tid}"
        elif row["kind"] == "future_GCM_consensus_mean" and row["gcm"] == "mean":
            family = f'{row["ssp"]}-{row["period"]}'
            identity = f'AGG|future_GCM_consensus_mean|{tid}|{row["ssp"]}|{row["period"]}|mean'
        else:
            continue
        path = (archive / row["relative_path"]).resolve(strict=True)
        key = family, tid
        if (not path.is_relative_to(archive) or key in products or tid not in species
                or species[tid].latin != row["species"] or row["product_id"] != identity
                or not row["independent_qc"] or path.stat().st_size != int(row["bytes"])
                or provenance["sources"][family][tid] != {"file": path.name, "sha256": row["sha256"]}
                or (family == "baseline" and release.source_sha256[tid] != row["sha256"])):
            raise ValueError(f"Incorrect or duplicate source identity for {identity}")
        products[key] = (path, row)
    if set(products) != {(family, tid) for family in ["baseline", *SCENARIOS] for tid in species}:
        raise ValueError("Expected 39 baselines and six five-GCM means for every species.")
    return products


def prepare(archive, city, directory):
    archive = archive.resolve(strict=True)
    release, demo_values = read_release(directory)
    ids = [item.id for item in release.dataset.species]
    prov_path = directory / "provenance.json"
    provenance = json.loads(prov_path.read_bytes())
    selection = archive / "manifests" / SELECTION
    profile_path = directory / "maxsss.json"
    profile = MaxSSSProfile.model_validate_json(profile_path.read_bytes())
    climate_path = directory / "climate.npz"
    if (release.dataset.release_id != FULL_RELEASE_ID or set(ids) != {f"PROD{i:04}" for i in range(1, 40)}
            or ids != provenance["speciesIds"] or release.data_version != provenance["dataVersion"]
            or any((s.scale, s.offset) != (1, 0) for s in release.dataset.species)
            or profile.dataVersion != release.data_version or set(profile.thresholds) != set(ids)
            or file_sha256(profile_path) != provenance["profileSha256"]
            or file_sha256(climate_path) != provenance["climateSha256"]
            or provenance["selection"] != SELECTION or file_sha256(selection) != provenance["selectionSha256"]):
        raise ValueError("Release, thresholds, selection or climate provenance is inconsistent.")
    table = city / "tables/species_threshold_maxsss_accepted_with_member_recovery.csv"
    accepted = rows(table)
    if (table.name != profile.thresholdTable or file_sha256(table) != profile.thresholdTableSha256
            or len(accepted) != len(ids) or any(not row["status"].endswith("_QC_PASS") for row in accepted)
            or {row["tid"]: float(row["threshold"]) for row in accepted} != profile.thresholds):
        raise ValueError("The accepted maxSSS thresholds do not match this release.")
    thresholds = np.array([profile.thresholds[tid] for tid in ids], dtype=np.float64)
    if not np.all((thresholds >= 0) & (thresholds <= 1)):
        raise ValueError("Expected finite 0–1 maxSSS thresholds.")
    products = selected_products(archive, selection, release, provenance)
    grid, indices, areas, info = city_geometry(city, products["baseline", ids[0]][0], release.grid)
    dy = indices // grid.width + grid.row0 - release.grid.row0
    dx = indices % grid.width + grid.col0 - release.grid.col0
    overlap = (dx >= 0) & (dx < release.grid.width) & (dy >= 0) & (dy < release.grid.height)
    if not overlap.any():
        raise ValueError("Jiaxing support has no shared cells with the baseline release.")
    with np.load(climate_path, allow_pickle=False) as cache:
        if set(cache.files) != set(SCENARIOS):
            raise ValueError("Incomplete release climate data.")
        demo_layers = {family: cache[family] for family in SCENARIOS}
    demo_layers["baseline"] = demo_values

    def extract(key):
        family, tid = key
        path, row = products[key]
        if file_sha256(path) != row["sha256"]:
            raise ValueError(f"Source SHA-256 mismatch: {path}")
        with rasterio.open(path) as src:
            check_grid(src)
            win = aligned_window(src, grid.bounds)
            if (win.col_off != grid.col0 or win.row_off != grid.row0 or src.dtypes != ("float32",)
                    or src.scales != (1.0,) or src.offsets != (0.0,)):
                raise ValueError(f"Source geometry or encoding mismatch: {path}")
            cells = src.read(1, window=win, masked=True).ravel()[indices]
            if np.ma.getmaskarray(cells).any():
                raise ValueError(f"Missing prediction on the city common support: {path}")
            values = np.asarray(cells)
        if not np.all((values >= 0) & (values <= 1)):
            raise ValueError(f"Invalid city prediction: {path}")
        shared = demo_layers[family][ids.index(tid), dy[overlap], dx[overlap]]
        if not np.array_equal(values[overlap], shared):
            raise ValueError(f"Predictions disagree on cells shared with the release: {path}")
        return key, values

    # Limit concurrent I/O on the source archive. Only the small city windows
    # are retained after hashing the complete original files.
    layers = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i, (key, values) in enumerate(pool.map(extract, products), start=1):
            layers[key] = values
            if i % 39 == 0:
                print(f"Verified {i}/{len(products)} source rasters", flush=True)

    def stack(family):
        return np.stack([layers[family, tid] for tid in ids])

    info.update({
        "releaseManifestSha256": file_sha256(directory / "manifest.json"),
        "releaseProvenanceSha256": file_sha256(prov_path), "selection": SELECTION,
        "selectionSha256": provenance["selectionSha256"], "thresholdTable": table.name,
        "thresholdTableSha256": profile.thresholdTableSha256, "profileSha256": provenance["profileSha256"],
        "sharedReleaseCells": int(overlap.sum()),
        "sources": {family: {tid: {field: products[family, tid][1][field]
                                   for field in ("product_id", "relative_path", "sha256")}
                             for tid in ids} for family in ["baseline", *SCENARIOS]},
    })
    meta = {"schema": 1, "releaseVersion": release.data_version, "cityId": CITY_ID,
            "speciesIds": ids, "scenarios": list(SCENARIOS), "grid": grid.model_dump(), "provenance": info}
    arrays = {"meta": np.asarray(json.dumps(meta, separators=(",", ":"))), "indices": indices, "areas": areas,
              "thresholds": thresholds, "baseline": stack("baseline"), "future": np.stack([stack(s) for s in SCENARIOS])}
    destination = directory / f"area-{CITY_ID}.npz"
    with NamedTemporaryFile(dir=directory, suffix=".npz", delete=False) as pending:
        temp = Path(pending.name)
    try:
        np.savez_compressed(temp, **arrays)
        with np.load(temp, allow_pickle=False) as check:
            if set(check.files) != set(arrays) or any(not np.array_equal(check[k], v) for k, v in arrays.items()):
                raise ValueError("Serialized area-comparison data failed verification.")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    print(f"{destination}: {len(indices)} native cells, {areas.sum():.9f} km2, {len(products)} raster sources.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--city", type=Path, default=CITY)
    parser.add_argument("--release", type=Path, default=RELEASE)
    args = parser.parse_args()
    prepare(args.archive, args.city, args.release)
