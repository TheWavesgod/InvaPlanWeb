"""Import a local dataset with python -m backend.import_dataset."""

import argparse
from pathlib import Path

from rasterio.errors import RasterioError

from .datasets import import_dataset


def main():
    parser = argparse.ArgumentParser(
        description="Prepare a local InvaPlan data release from GeoTIFFs / 从 GeoTIFF 导入 InvaPlan 本地数据包"
    )
    parser.add_argument("--manifest", required=True, type=Path, help="Dataset manifest / 数据清单")
    parser.add_argument("--source-dir", required=True, type=Path, help="Input TIFF folder / 输入 TIFF 目录")
    parser.add_argument("--output", required=True, type=Path, help="New release folder / 新数据包目录")
    args = parser.parse_args()
    try:
        release = import_dataset(args.manifest, args.source_dir, args.output)
    except (OSError, ValueError, RasterioError) as error:
        parser.exit(1, f"Import failed / 导入失败\n{error}\n")
    print(f"Imported / 已导入 {release.dataset.release_id}")
    print(f"{len(release.dataset.species)} species / 物种, {release.grid.width} × {release.grid.height} cells / 网格")
    print(f"{release.data_version}\n{args.output.resolve()}")


if __name__ == "__main__":
    main()
