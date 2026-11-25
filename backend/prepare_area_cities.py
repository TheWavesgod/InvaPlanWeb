"""Publish private 62-city comparison bundles from the corrected Chapter 4 release."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.windows import Window, bounds

from .area_comparison import AreaComparison, BUDGETS, HOLDOUTS, JIAXING_SUPPORT_SHA256
from .climate import SCENARIOS
from .datasets import Grid, file_sha256
from .prepare_area_comparison import (
    ARCHIVE, CITY, CITY_ID, RELEASE, aligned_window, cell_areas, check_grid, verified_inputs,
)
from .prepare_jiaxing import rows


@dataclass
class CitySupport:
    record: dict
    grid: Grid
    indices: np.ndarray
    areas: np.ndarray
    span: slice
    digest: str
    provenance: dict


def supports(city, reference, demo):
    zone_path = city / "support/city_zone_common.tif"
    coverage_path = city / "support/city_coverage.csv"
    records = sorted(rows(coverage_path), key=lambda row: int(row["adcode"]))
    codes = [record["adcode"] for record in records]
    if (len(set(codes)) != 62 or len(codes) != 62 or any(len(code) != 6 or not code.isdecimal() for code in codes)
            or any(not row["name_cn"] or not row["name_en"] for row in records)):
        raise ValueError("Expected 62 distinct municipal adcodes and bilingual names.")
    common = {"zoneFile": zone_path.name, "zoneSha256": file_sha256(zone_path),
              "coverageFile": coverage_path.name, "coverageSha256": file_sha256(coverage_path),
              "areaMethod": "WGS84 ellipsoid surface bounded by native parallels and meridians"}
    with rasterio.open(reference) as source, rasterio.open(zone_path) as zones:
        check_grid(source)
        check_grid(zones)
        demo_win = aligned_window(source, demo.bounds)
        if (demo_win.row_off, demo_win.col_off) != (demo.row0, demo.col0):
            raise ValueError("Reference raster does not reproduce the release's native pixel identifiers.")
        source_win = aligned_window(source, zones.bounds)
        if (source_win.width, source_win.height) != (zones.width, zones.height):
            raise ValueError("City common zone raster and prediction geometry disagree.")
        data = zones.read(1, masked=True)
        y, x = np.nonzero(~np.ma.getmaskarray(data))
        labels = data.data[y, x]
        if not np.array_equal(np.unique(labels), np.arange(1, 63)):
            raise ValueError("Common support must contain exactly the archived 62 city-zone identifiers.")
        # Stable grouping keeps absolute rows and columns in native row-major order within each city.
        order = np.argsort(labels, kind="stable")
        y = y[order].astype(np.int32) + source_win.row_off
        x = x[order].astype(np.int32) + source_win.col_off
        sizes = np.bincount(labels, minlength=63)[1:]
        cities = []
        start = 0
        for zone, (record, size) in enumerate(zip(records, sizes, strict=True), start=1):
            span = slice(start, start + int(size))
            start = span.stop
            yy, xx = y[span], x[span]
            top, left = int(yy.min()), int(xx.min())
            height, width = int(yy.max()) - top + 1, int(xx.max()) - left + 1
            window = Window(left, top, width, height)
            grid = Grid(width=width, height=height, row0=top, col0=left, bounds=bounds(window, source.transform))
            indices = (yy - top) * width + xx - left
            areas = cell_areas(grid, indices)
            expected = float(record["valid_area_km2"])
            if (size != int(record["valid_cells"]) or np.any(np.diff(indices) <= 0)
                    or not np.isfinite(areas).all() or np.any(areas <= 0)
                    or not math.isfinite(expected) or abs(areas.sum() - expected) > 0.01):
                raise ValueError(f"Native support or area disagrees with published coverage for {record['adcode']}.")
            digest = sha256(np.column_stack((yy, xx)).astype("<i4").tobytes()).hexdigest()
            if record["adcode"] == CITY_ID and digest != JIAXING_SUPPORT_SHA256:
                raise ValueError("Jiaxing cell identifiers disagree with its independently verified support.")
            info = dict(common, zoneId=zone, expectedCells=int(size), publishedAreaKm2=expected)
            cities.append(CitySupport(record, grid, indices, areas, span, digest, info))
        window = Window(int(x.min()), int(y.min()), int(x.max() - x.min()) + 1, int(y.max() - y.min()) + 1)
        return cities, y, x, window, bounds(window, source.transform)


def extract(cube, products, ids, demo_layers, shared, y, x, window, extent):
    families = ["baseline", *SCENARIOS]
    local_y, local_x = y - window.row_off, x - window.col_off
    overlap, demo_y, demo_x = shared

    def read(key):
        family, tid = key
        path, row = products[key]
        # Hash and decode the same bytes; read the archive once per product across all cities.
        content = path.read_bytes()
        if sha256(content).hexdigest() != row["sha256"]:
            raise ValueError(f"Prediction SHA-256 mismatch: {path}")
        with MemoryFile(content) as memory, memory.open() as src:
            check_grid(src)
            if (aligned_window(src, extent) != window or src.dtypes != ("float32",)
                    or src.scales != (1.0,) or src.offsets != (0.0,)):
                raise ValueError(f"Prediction geometry or encoding mismatch: {path}")
            selected = src.read(1, window=window, masked=True)[local_y, local_x]
            values = np.asarray(selected)
            if np.ma.getmaskarray(selected).any() or not np.all((values >= 0) & (values <= 1)):
                raise ValueError(f"Masked, nonfinite or out-of-range prediction on municipal support: {path}")
        i = ids.index(tid)
        if not np.array_equal(values[overlap], demo_layers[family][i, demo_y, demo_x]):
            raise ValueError(f"Shared sample-window predictions do not match for {path}")
        cube[families.index(family), i] = values

    with rasterio.Env(GDAL_CACHEMAX=64 * 1024**2), ThreadPoolExecutor(max_workers=3) as pool:
        for count, _ in enumerate(pool.map(read, products), 1):
            if count % 13 == 0:
                print(f"Verified and extracted {count}/{len(products)} predictions across all cities.", flush=True)


def write_city(directory, legacy_dir, item, cube, ids, thresholds, version, source_info, overlap):
    city_id = item.record["adcode"]
    info = dict(item.provenance, **source_info, sharedReleaseCells=int(overlap[item.span].sum()))
    meta = {"schema": 1, "releaseVersion": version, "cityId": city_id, "speciesIds": ids,
            "scenarios": list(SCENARIOS), "grid": item.grid.model_dump(mode="json"), "provenance": info}
    arrays = {"indices": item.indices, "areas": item.areas, "thresholds": thresholds,
              "baseline": np.ascontiguousarray(cube[0, :, item.span]),
              "future": np.ascontiguousarray(cube[1:, :, item.span])}
    name = f"area-{city_id}.npz"
    path = directory / name
    legacy = legacy_dir / name
    if city_id == CITY_ID and legacy.is_file():
        # Preserve the pre-existing Jiaxing version only after every new input and its lineage agree.
        shutil.copyfile(legacy, path)
    else:
        np.savez_compressed(path, meta=np.asarray(json.dumps(meta, separators=(",", ":"))), **arrays)
    with np.load(path, allow_pickle=False) as check:
        if (set(check.files) != {"meta", *arrays} or json.loads(check["meta"].item()) != meta
                or any(not np.array_equal(check[key], value) for key, value in arrays.items())):
            raise ValueError(f"Bundle does not reproduce extracted native inputs for {city_id}.")
    digest = file_sha256(path)
    engine = AreaComparison(meta, item.grid, item.indices, item.areas, arrays["baseline"].astype(np.float64),
                            arrays["future"].astype(np.float64), thresholds, f"city-area-v1-{digest[:16]}")
    total = math.fsum(item.areas)
    results = {}
    for holdout in HOLDOUTS:
        results[holdout] = {}
        for budget in BUDGETS:
            comparison = engine.comparison(budget, holdout)
            for allocation in comparison["allocations"].values():
                if (abs(math.fsum(allocation) - total * budget / 100) > 1e-8
                        or np.any(np.asarray(allocation) > item.areas) or min(allocation) < 0):
                    raise ValueError(f"Allocation misses its native area budget for {city_id}/{holdout}/{budget}.")
            results[holdout][str(budget)] = {key: comparison[key] for key in ("summary", "eligibleGainSpecies")}
    return {"cityId": city_id, "nameZh": item.record["name_cn"], "nameEn": item.record["name_en"],
            "cellCount": len(item.indices), "areaKm2": total, "file": name, "sha256": digest,
            "supportSha256": item.digest, "results": results}


def prepare(archive, city, directory, output):
    release, ids, thresholds, products, demo_layers, info = verified_inputs(archive, city, directory)
    demo = release.grid
    cities, y, x, window, extent = supports(city, products["baseline", ids[0]][0], demo)
    dy, dx = y - demo.row0, x - demo.col0
    overlap = (dy >= 0) & (dy < demo.height) & (dx >= 0) & (dx < demo.width)
    shared = overlap, dy[overlap], dx[overlap]
    print(f"Checked {len(cities)} city masks, {len(y):,} native cells. Extracting {len(products)} rasters.",
          flush=True)
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".area-build-", dir=output) as pending:
        stage = Path(pending)
        bundle_dir = stage / "bundles"
        bundle_dir.mkdir()
        cube = np.memmap(stage / "values.bin", mode="w+", dtype=np.float32, shape=(1 + len(SCENARIOS), len(ids), len(y)))
        try:
            extract(cube, products, ids, demo_layers, shared, y, x, window, extent)
            records = []
            for n, item in enumerate(cities, 1):
                records.append(write_city(bundle_dir, directory, item, cube, ids, thresholds,
                                          release.data_version, info, overlap))
                print(f"Checked {n}/{len(cities)} city bundles and all nine comparisons: {item.record['adcode']}",
                      flush=True)
        finally:
            del cube
        generation = "bundles-" + sha256("".join(r["sha256"] for r in records).encode()).hexdigest()[:24]
        destination = output / generation
        try:
            bundle_dir.rename(destination)
        except OSError:
            # A repeat or concurrent build can produce the same immutable generation.
            if not destination.is_dir() or any(file_sha256(destination / r["file"]) != r["sha256"] for r in records):
                raise
        for record in records:
            record["file"] = f"{generation}/{record['file']}"
        manifest = {"schema": 1, "releaseVersion": release.data_version,
                    "thresholdTableSha256": info["thresholdTableSha256"], "cities": records}
        index = stage / "area-cities.json"
        index.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                         encoding="utf-8")
        index.replace(output / index.name)
    print(f"Published {output / 'area-cities.json'} ({len(records)} cities, {len(y):,} cells).", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--city", type=Path, default=CITY)
    parser.add_argument("--release", type=Path, default=RELEASE)
    parser.add_argument("--output", type=Path, required=True, help="Private local output directory")
    args = parser.parse_args()
    prepare(args.archive, args.city, args.release, args.output)
