from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .area_comparison import load_area_comparison, router as area_comparison_router
from .climate import SCENARIOS, load_climate, load_prepared_climate
from .policy import build_run, evaluate, inspect_cell, shortlist_summary
from .datasets import DEFAULT_MANIFEST
from .sample import FULL_RELEASE_ID, MAX_K, Sample, cell_id, load_prepared_sample, load_sample


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
    climate_dir = os.environ.get("INVAPLAN_CLIMATE_DIR")
    if release_dir and app.state.sample.dataset.release_id == FULL_RELEASE_ID:
        app.state.climate = load_prepared_climate(Path(release_dir), app.state.sample)
        app.state.area_comparison = load_area_comparison(Path(release_dir), app.state.sample, app.state.climate)
    else:
        app.state.climate = load_climate(Path(climate_dir), app.state.sample) if climate_dir else {}
        app.state.area_comparison = None
    yield


app = FastAPI(title="InvaPlan pilot", lifespan=lifespan)
app.include_router(area_comparison_router)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speciesIds: list[str] = Field(min_length=1)
    k: int = Field(strict=True, ge=1, le=MAX_K)
    excludedCellIds: list[str]
    tieBreak: Literal["cell_id", "spatial_spread"] = "cell_id"

    def check_catalog(self, sample: Sample):
        if len(set(self.speciesIds)) != len(self.speciesIds) or any(i not in sample.species_index for i in self.speciesIds):
            raise HTTPException(422, "请选择互不重复的已登记物种。")
        if len(set(self.excludedCellIds)) != len(self.excludedCellIds) or any(i not in sample.index_by_id for i in self.excludedCellIds):
            raise HTTPException(422, "排除网格必须位于当前样例窗口，且编号不能重复。")


class ShortlistInput(RunInput):
    dataVersion: str
    policyVersion: str
    originalCellIds: list[str] = Field(max_length=MAX_K)
    finalCellIds: list[str] = Field(max_length=MAX_K)


@app.post("/api/shortlist/comparison")
def compare_shortlist(inputs: ShortlistInput, request: Request):
    sample: Sample = request.app.state.sample
    inputs.check_catalog(sample)
    if (inputs.dataVersion, inputs.policyVersion) != (sample.data_version, sample.policy_version):
        raise HTTPException(409, "shortlist_version_mismatch")
    evidence = evaluate(sample, inputs.speciesIds, inputs.excludedCellIds)
    summaries = {}
    for name, ids in (("original", inputs.originalCellIds), ("final", inputs.finalCellIds)):
        if len(ids) > inputs.k or len(set(ids)) != len(ids):
            raise HTTPException(422, "invalid_shortlist")
        for key in ids:
            i = sample.index_by_id.get(key)
            if i is None or evidence.excluded[i] or not evidence.complete[i]:
                raise HTTPException(422, "invalid_shortlist")
        summaries[name] = shortlist_summary(sample, evidence, ids)
    return summaries


@app.get("/api/catalog")
def catalog(request: Request):
    return {
        **request.app.state.sample.catalog(),
        "hasAreaComparison": request.app.state.area_comparison is not None,
        "climateScenarios": [SCENARIOS[key] for key in request.app.state.climate],
    }


@app.get("/api/layers/{species_id}.png")
def layer(species_id: str, request: Request):
    sample: Sample = request.app.state.sample
    if species_id not in sample.layers:
        raise HTTPException(404, "没有找到该图层。")
    return Response(sample.layers[species_id], media_type="image/png")


@app.get("/api/climate/{scenario}/{kind}/{species_id}.png")
def climate_layer(scenario: str, kind: str, species_id: str, request: Request):
    try:
        _, layers = request.app.state.climate[scenario]
        image = layers[kind, species_id]
    except KeyError:
        raise HTTPException(404, "没有找到该图层。") from None
    return Response(image, media_type="image/png")


@app.post("/api/runs")
def run(inputs: RunInput, request: Request):
    sample: Sample = request.app.state.sample
    inputs.check_catalog(sample)
    return build_run(sample, evaluate(sample, inputs.speciesIds, inputs.excludedCellIds), inputs.model_dump(exclude_unset=True))


@app.post("/api/cells/{row}/{col}")
def cell(row: int, col: int, inputs: RunInput, request: Request, climate: str | None = None):
    sample: Sample = request.app.state.sample
    inputs.check_catalog(sample)
    index = sample.index_by_id.get(cell_id(row, col))
    if index is None:
        raise HTTPException(404, "网格在当前样例窗口之外。")
    detail = inspect_cell(sample, evaluate(sample, inputs.speciesIds, inputs.excludedCellIds), index)
    if climate is not None:
        if climate not in request.app.state.climate:
            raise HTTPException(404, "没有找到该图层。")
        future, _ = request.app.state.climate[climate]
        detail["climateScenario"] = climate
        detail["futureValues"] = {
            species.id: float(value) if np.isfinite(value) else None
            for species, value in zip(sample.dataset.species, future.reshape(len(sample.dataset.species), -1)[:, index], strict=True)
        }
    return detail


if (ROOT / "dist/assets").is_dir():
    app.mount("/assets", StaticFiles(directory=ROOT / "dist/assets"), name="assets")


@app.get("/", include_in_schema=False)
def home():
    index = ROOT / "dist/index.html"
    if not index.is_file():
        raise HTTPException(503, "请先运行 pnpm build，或用 pnpm dev 启动前端。")
    return FileResponse(index)
