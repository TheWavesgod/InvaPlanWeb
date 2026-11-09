"""Read a fixed, aligned window from the nine supplied GeoTIFFs."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import struct
import warnings
import zlib

import numpy as np
import rasterio
from rasterio.windows import Window, bounds, from_bounds


SPECIES = (
    ("PROD0037", "互花米草", "Spartina alterniflora"),
    ("PROD0020", "黄顶菊", "Flaveria bidentis"),
    ("PROD0005", "反枝苋", "Amaranthus retroflexus"),
    ("PROD0038", "钻叶紫菀", "Symphyotrichum subulatum"),
    ("PROD0017", "一年蓬", "Erigeron annuus"),
    ("PROD0022", "圆叶牵牛", "Ipomoea purpurea"),
    ("PROD0012", "鬼针草", "Bidens pilosa"),
    ("PROD0018", "小蓬草", "Erigeron canadensis"),
    ("PROD0004", "长芒苋", "Amaranthus palmeri"),
)
SPECIES_INDEX = {code: i for i, (code, _, _) in enumerate(SPECIES)}
PILOT_BOUNDS = (120.45, 30.40, 121.25, 31.05)
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
            "name": "嘉兴周边样例窗口",
            "dataVersion": self.data_version,
            "policyVersion": POLICY_VERSION,
            "baselinePeriod": None,
            "species": [
                {"id": code, "name": name, "latin": latin, "exploratory": code == "PROD0004"}
                for code, name, latin in SPECIES
            ],
            "grid": {
                "bounds": self.bounds, "width": self.width, "height": self.height,
                "row0": self.row0, "col0": self.col0,
            },
            "thresholds": {
                code: {"value": float(self.thresholds[i]), "validCount": int(self.valid_counts[i])}
                for i, (code, _, _) in enumerate(SPECIES)
            },
            "maxK": MAX_K,
        }


def load_sample(directory: Path) -> Sample:
    parts, layers = [], {}
    reference = None
    for code, _, latin in SPECIES:
        path = directory / f"{code}_{latin.replace(' ', '_')}_current_S2_30s.tif"
        with rasterio.open(path, "r") as source:
            grid = (source.crs, source.transform, source.width, source.height)
            if source.crs is None or source.crs.to_epsg() != 4326 or source.count != 1:
                raise ValueError(f"{path.name} must be a single-band WGS 84 raster")
            if source.scales[0] != 1 or source.offsets[0] != 0:
                raise ValueError(f"{path.name} is not in the verified sample scale")
            if reference is None:
                reference = grid
                window = from_bounds(*PILOT_BOUNDS, transform=source.transform)
                window = Window(*(round(v) for v in window.flatten()))
                if (window.col_off < 0 or window.row_off < 0 or
                    window.col_off + window.width > source.width or
                    window.row_off + window.height > source.height):
                    raise ValueError("The pilot window is outside the source grid")
                window_bounds = bounds(window, source.transform)
            elif grid != reference:
                raise ValueError(f"{path.name} does not align with the first source raster")
            array = source.read(1, window=window, masked=True).astype(np.float32).filled(np.nan)
            array[~np.isfinite(array)] = np.nan
            valid = array[np.isfinite(array)]
            if valid.size == 0 or valid.min() < 0 or valid.max() > 1:
                raise ValueError(f"{path.name} has no usable values or is outside the declared 0–1 scale")
            parts.append(array)
            layers[code] = _raster_png(array)

    values = np.stack(parts)
    valid_counts = np.isfinite(values).sum(axis=(1, 2))
    thresholds = np.array([np.quantile(a[np.isfinite(a)].astype("float64"), 0.9, method="linear") for a in values])
    digest = sha256(repr((SPECIES, window_bounds, window.flatten())).encode())
    digest.update(values.astype("<f4", copy=False).tobytes())
    row0, col0 = int(window.row_off), int(window.col_off)
    index_by_id = {
        cell_id(row0 + y, col0 + x): y * values.shape[2] + x
        for y in range(values.shape[1]) for x in range(values.shape[2])
    }
    return Sample(values, thresholds, valid_counts, window_bounds, row0, col0,
                  "sample-window-" + digest.hexdigest()[:12], layers, index_by_id)
