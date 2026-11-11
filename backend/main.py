from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .policy import build_run, evaluate, inspect_cell
from .datasets import DEFAULT_MANIFEST
from .sample import MAX_K, Sample, cell_id, load_prepared_sample, load_sample


ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    release_dir = os.environ.get("INVAPLAN_RELEASE_DIR")
    raster_dir = os.environ.get("INVAPLAN_RASTER_DIR")
    if release_dir:
        app.state.sample = load_prepared_sample(Path(release_dir))
    elif raster_dir:
        manifest = os.environ.get("INVAPLAN_MANIFEST")
        app.state.sample = load_sample(Path(raster_dir), Path(manifest) if manifest else DEFAULT_MANIFEST)
    else:
        raise RuntimeError("Set INVAPLAN_RELEASE_DIR to an imported release or INVAPLAN_RASTER_DIR to a TIFF directory.")
    yield


app = FastAPI(title="InvaPlan pilot", lifespan=lifespan)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speciesIds: list[str] = Field(min_length=1)
    k: int = Field(strict=True, ge=1, le=MAX_K)
    excludedCellIds: list[str]

    def check_catalog(self, sample: Sample):
        if len(set(self.speciesIds)) != len(self.speciesIds) or any(i not in sample.species_index for i in self.speciesIds):
            raise HTTPException(422, "请选择互不重复的已登记物种。")
        if len(set(self.excludedCellIds)) != len(self.excludedCellIds) or any(i not in sample.index_by_id for i in self.excludedCellIds):
            raise HTTPException(422, "排除网格必须位于当前样例窗口，且编号不能重复。")


@app.get("/api/catalog")
def catalog(request: Request):
    return request.app.state.sample.catalog()


@app.get("/api/layers/{species_id}.png")
def layer(species_id: str, request: Request):
    sample: Sample = request.app.state.sample
    if species_id not in sample.layers:
        raise HTTPException(404, "没有找到该图层。")
    return Response(sample.layers[species_id], media_type="image/png")


@app.post("/api/runs")
def run(inputs: RunInput, request: Request):
    sample: Sample = request.app.state.sample
    inputs.check_catalog(sample)
    return build_run(sample, evaluate(sample, inputs.speciesIds, inputs.excludedCellIds), inputs.model_dump())


@app.post("/api/cells/{row}/{col}")
def cell(row: int, col: int, inputs: RunInput, request: Request):
    sample: Sample = request.app.state.sample
    inputs.check_catalog(sample)
    index = sample.index_by_id.get(cell_id(row, col))
    if index is None:
        raise HTTPException(404, "网格在当前样例窗口之外。")
    return inspect_cell(sample, evaluate(sample, inputs.speciesIds, inputs.excludedCellIds), index)


if (ROOT / "dist/assets").is_dir():
    app.mount("/assets", StaticFiles(directory=ROOT / "dist/assets"), name="assets")


@app.get("/", include_in_schema=False)
def home():
    index = ROOT / "dist/index.html"
    if not index.is_file():
        raise HTTPException(503, "请先运行 pnpm build，或用 pnpm dev 启动前端。")
    return FileResponse(index)
