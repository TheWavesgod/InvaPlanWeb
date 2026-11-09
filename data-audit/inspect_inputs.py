"""Read-only, local-file M0 inspection. Never modifies source data.

Raster inspection requires rasterio and numpy. CSV inspection uses stdlib only.
Example: python inspect_inputs.py --raster D:/data/sdm.tif --out report.json
"""

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def file_info(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Expected a local file: {path}")
    stat = path.stat()
    return path, {
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def inspect_raster(path, band, window_size):
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    path, result = file_info(path)
    with rasterio.open(path, "r") as src:
        if not 1 <= band <= src.count:
            raise ValueError(f"Band {band} outside 1..{src.count}")
        warnings = [
            "Window samples are not whole-raster statistics or an unbiased random sample.",
            "Numeric range does not establish probability, units, or scientific meaning.",
        ]
        if src.crs is None:
            warnings.append("Missing CRS: do not guess it from coordinates.")
        elif src.crs.is_geographic:
            warnings.append("Geographic CRS: pixel dimensions are angular, not metres.")
        if src.nodatavals[band - 1] is None:
            warnings.append("No numeric NoData value declared; inspect masks and provenance.")
        if src.nodatavals[band - 1] == 0:
            warnings.append("NoData is zero: verify that valid zero predictions were not excluded.")
        if src.transform.b != 0 or src.transform.d != 0:
            warnings.append("Rotated/sheared grid: resolution alone does not describe alignment.")
        if np.issubdtype(np.dtype(src.dtypes[band - 1]), np.complexfloating):
            raise ValueError("Complex-valued raster is not supported by this SDM inspector")

        height = min(window_size, src.height)
        width = min(window_size, src.width)
        rows = sorted({0, (src.height - height) // 2, src.height - height})
        cols = sorted({0, (src.width - width) // 2, src.width - width})
        windows, values = [], []
        total = masked = nonfinite = 0
        for row in rows:
            for col in cols:
                sample = src.read(band, window=Window(col, row, width, height), masked=True)
                valid = sample.compressed().astype("float64")
                total += sample.size
                masked += int(np.ma.getmaskarray(sample).sum())
                finite = np.isfinite(valid)
                nonfinite += int((~finite).sum())
                values.append(valid[finite])
                windows.append({"row": row, "col": col, "height": height, "width": width})
        raw = np.concatenate(values)

        def stats(array):
            if array.size == 0:
                return None
            return {
                "min": float(array.min()),
                "p01": float(np.quantile(array, 0.01)),
                "median": float(np.median(array)),
                "p99": float(np.quantile(array, 0.99)),
                "max": float(array.max()),
            }

        scale, offset = src.scales[band - 1], src.offsets[band - 1]
        physical = raw * scale + offset
        finite_physical = np.isfinite(physical)
        if not finite_physical.all():
            warnings.append("Applying declared scale/offset produced non-finite sample values.")
        if raw.size == 0:
            warnings.append("No finite, unmasked samples: check coverage with targeted windows.")
        result.update({
            "driver": src.driver,
            "crs": src.crs.to_string() if src.crs else None,
            "crs_wkt": src.crs.to_wkt() if src.crs else None,
            "width": src.width,
            "height": src.height,
            "band_count": src.count,
            "band_descriptions": src.descriptions,
            "dtypes": src.dtypes,
            "nodata_values": src.nodatavals,
            "scales": src.scales,
            "offsets": src.offsets,
            "band_units": src.units,
            "resolution_in_crs_units": list(src.res),
            "bounds_in_crs_units": list(src.bounds),
            "affine_transform": list(src.transform),
            "block_shapes": src.block_shapes,
            "sample": {
                "band": band,
                "windows": windows,
                "pixel_reads_including_overlapping_windows": total,
                "masked_pixel_reads": masked,
                "unmasked_nonfinite_pixel_reads": nonfinite,
                "raw_value_statistics": stats(raw),
                "declared_scale_offset_statistics": stats(physical[finite_physical]),
            },
            "warnings": warnings,
        })
    return result


def inspect_csv(path, encoding, delimiter, row_limit):
    path, result = file_info(path)
    warnings = []
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, None)
        if header is None:
            result.update({"columns": [], "rows_inspected": 0, "warnings": ["Empty file"]})
            return result
        if len(set(header)) != len(header):
            warnings.append("Duplicate column names")
        if any(not name.strip() for name in header):
            warnings.append("Empty column names")
        missing = [0] * len(header)
        count = malformed = 0
        reached_end = False
        for _ in range(row_limit):
            row = next(reader, None)
            if row is None:
                reached_end = True
                break
            count += 1
            if len(row) != len(header):
                malformed += 1
            for index in range(len(header)):
                if index >= len(row) or not row[index].strip():
                    missing[index] += 1
        if not reached_end:
            reached_end = next(reader, None) is None
    result.update({
        "encoding_used": encoding,
        "delimiter_used": delimiter,
        "columns": header,
        "rows_inspected": count,
        "whole_table_inspected": reached_end,
        "rows_with_wrong_column_count": malformed,
        "blank_cells_by_column_position": missing,
        "warnings": warnings + [
            "No row values exported; missing counts cover blank cells only, not NA/sentinel codes.",
            "Fields, units, species identifiers, duplicates, and evaluation meaning need later review.",
        ],
    })
    return result


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raster", action="append", default=[])
    parser.add_argument("--csv", action="append", default=[])
    parser.add_argument("--band", type=int, default=1)
    parser.add_argument("--window-size", type=int, default=128)
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--csv-row-limit", type=int, default=10000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.raster and not args.csv:
        parser.error("Provide at least one --raster or --csv")
    if not 1 <= args.window_size <= 1024 or args.csv_row_limit < 1:
        parser.error("window-size must be 1..1024; csv-row-limit must be positive")
    if len(args.delimiter) != 1:
        parser.error("delimiter must be a single character")
    output = Path(args.out).expanduser().resolve()
    inputs = [Path(p).expanduser().resolve() for p in args.raster + args.csv]
    if output in inputs or output.exists():
        parser.error("Output must be a new file, separate from all inputs")
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "rasters": [], "tables": []}
    errors = False
    for kind, paths, inspect in [
        ("rasters", args.raster, lambda p: inspect_raster(p, args.band, args.window_size)),
        ("tables", args.csv, lambda p: inspect_csv(p, args.encoding, args.delimiter, args.csv_row_limit)),
    ]:
        for path in paths:
            try:
                report[kind].append(inspect(path))
            except Exception as exc:
                errors = True
                report[kind].append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
    with output.open("x", encoding="utf-8") as handle:
        json.dump(json_safe(report), handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"Report: {output}")
    print("Inspection errors present" if errors else "Inspection completed; scientific validity not yet established")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
