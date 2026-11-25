# InvaPlan · 入侵植物优先调查区识别 / Priority Survey Areas for Invasive Plants

本地交互原型。嘉兴案例支持研究中的全部 39 种入侵植物，默认全选，可搜索中文名、学名或物种编号并调整调查对象。React 和 Leaflet 显示地图，FastAPI 计算网格优先分与调查清单。

## 62 城等面积选址比较 / Equal-area comparison across 62 cities

打开「等面积选址比较」或访问首页的 `#area-comparison`。选择一个面积预算（5%、10% 或 20%）和一条留出评估的 SSP 路径。汇总表按**各城市自己的有效预测面积**计算预算。点城市名进入地图，选物种可查看三种策略覆盖和遗漏的持续或新增适生区。覆盖物种数的分母是该城在留出路径下有新增适生区的物种数。

Open **Equal-area comparison** and select an area budget and a climate pathway to hold out. Each city allocates the same percentage of its own valid land area to each strategy. Open a city to inspect individual species, the underlying grid evidence and the areas covered or missed by the fixed selections. In the species-coverage column, the denominator counts species with newly suitable habitat in that city under the held-out pathway.

从私有归档首次准备时，先按下节安装依赖。将 `INVAPLAN_ARCHIVE` 和 `INVAPLAN_CITY` 分别设为预测归档和市域数据目录。已有 `data/jiaxing-39/manifest.json` 时可跳过第一条导入命令。所有城市共用现有的 39 种模型输出。

```powershell
.\.venv\Scripts\python.exe -B -m backend.prepare_jiaxing "$env:INVAPLAN_ARCHIVE" "$env:INVAPLAN_CITY" .\data\jiaxing-39
$areaDir = Join-Path $env:LOCALAPPDATA 'InvaPlan\area-cities'
.\.venv\Scripts\python.exe -B -m backend.prepare_area_cities --archive "$env:INVAPLAN_ARCHIVE" --city "$env:INVAPLAN_CITY" --release .\data\jiaxing-39 --output "$areaDir"
pnpm build
.\Start-Demo.ps1 -ReleaseDir .\data\jiaxing-39 -AreaDir "$areaDir"
```

这里的 Windows 输出目录位于本机用户目录，独立于 Git 仓库和 OneDrive 工作区。日常启动运行 `.\Start-Demo.ps1` 即可。九物种样例和已有的嘉兴单城数据包也可以单独使用。

三个选址规则共用全部 39 种和同一面积预算。A1 使用其余两个 SSP、两个时期的多物种平均适宜度，A2 使用其相对基准期的变化。B 对各物种分别识别持续达标和新达标网格，按组内百分位排序，每格取其最高百分位。面积按原生格实际大小累计，最后一格按剩余额度计入。切换物种只改变证据图层与覆盖统计。留出情景的两个时期均达到物种阈值才计入未来适宜面积。

To prepare the comparison locally, point the two environment variables above at the authorised prediction archive and city data directory. Skip the first import if the 39-species release is already present. The second command prepares the city index and bundles outside the checkout. For direct FastAPI startup, set `INVAPLAN_RELEASE_DIR` to the base release and `INVAPLAN_AREA_DIR` to the folder containing `area-cities.json`. CSV gives coverage by species and strategy. GeoJSON contains native cell polygons and the area credited to each strategy, including a fractional last cell where needed.

## 导入本地数据 / Import local data

从项目根目录运行。首次配置 Python 环境时执行以下命令。 / Run from the repository root. Set up the Python environment once.

```powershell
uv venv .venv --python 3.14 --cache-dir .uv-cache
uv pip install --python '.\.venv\Scripts\python.exe' --cache-dir .uv-cache -r requirements.txt
```

用仓库中的九物种清单读取外部 GeoTIFF。输出目录应为新目录。 / Import the external GeoTIFFs using the sample manifest. Use a new folder for each output.

```powershell
& .\.venv\Scripts\python.exe -B -m backend.import_dataset --manifest datasets/jiaxing-sample.json --source-dir 'E:\Project\Website\InvaPlanDataSample\current_S2_30s' --output data/jiaxing-sample
```

发布包含 `manifest.json` 和 `values.npz`，记录原生网格、数据版本和源文件 SHA-256。服务启动时检查数据包校验值。增加物种时，在源清单中填写双语名称、学名、源文件、尺度和偏移，再导入到新目录。默认参与评分的物种和打开时显示的图层由清单指定。 / Each release contains `manifest.json` and `values.npz` with the native grid, version and source checksums. The server validates the package at startup. Add species to the input manifest with bilingual names and raster details, then import a new release. The manifest sets the default species and map layer.

## Docker 启动 / Run with Docker

Docker 引擎就绪后，从项目根目录运行。Compose 将本地发布包只读挂载到容器。 / With a Docker engine running, start Compose from the repository root. It mounts the local release read-only.

```powershell
$env:INVAPLAN_RELEASE_DIR = './data/jiaxing-39'
docker compose up --build
```

打开 [Docker 工作台 / Docker workspace](http://127.0.0.1:8766/)。`INVAPLAN_RELEASE_DIR` 可指定另一个已导入目录，`INVAPLAN_PORT` 可调整宿主机端口。 / Set `INVAPLAN_RELEASE_DIR` for another imported release and `INVAPLAN_PORT` to change the host port.

## PowerShell 启动 / Run with PowerShell

```powershell
pnpm install --store-dir .pnpm-store
pnpm build
.\Start-Demo.ps1 -ReleaseDir .\data\jiaxing-sample
```

打开 [本地工作台 / Local workspace](http://127.0.0.1:8765/)。可在另一个终端运行 `pnpm dev` 修改界面。 / Run `pnpm dev` in another terminal for frontend development.

## 未来情景 / Future scenarios

39 种版本内置 11 月 20 日选定的情景窗口。以下命令复现早期九种样例使用的情景版本。 / The 39-species package includes the climate windows selected on 20 November. To reproduce the earlier nine-species climate release, use:

```powershell
.\Start-Demo.ps1 -RasterDir .\data\current_S2_30s -ClimateDir 'K:\C4_PUBLICATION_MASTER\zenodo_release'
```

地图提供 SSP1-2.6、SSP2-4.5 和 SSP5-8.5，覆盖 2041–2060 和 2081–2100。未来层为五个模型的平均适宜度，变化层为未来均值减去基准值。变化图色标在 ±0.25 饱和，网格详情显示实际数值。调查评分基于基准期。 / The map shows the five-model mean and its difference from the baseline for three SSPs and two periods. The change legend saturates at ±0.25. Cell details show the underlying values. Survey scores use the baseline.

## 演示流程

调查工作台默认选中当前发布包指定的物种。生成方案 A，点击清单项查看网格证据。蓝色网格与原始推荐末位同分，橙色网格属于当前工作清单。打开「同分候选」，可用一个蓝色网格替换清单中的同分网格，填写理由后清单数量保持不变。JSON 和 GeoJSON 保留完整原始推荐与人工调整，CSV 同时记录原始推荐编号和调整历史。复制为方案 B，改变数量上限，再生成一次。比较页会在同一张地图上用三种颜色标出交集、A 独有和 B 独有。工作清单支持带理由的移出和人工加入。确认后可导出 CSV、GeoJSON 或含完整决定的 JSON。已确认方案保存在当前浏览器，同一数据和规则版本下刷新可继续查看。

## 数据与规则

首版读取嘉兴周边的一个矩形窗口（东经 120.45° 至 121.25°、北纬 30.40° 至 31.05°），用原始网格行列编号保留可追溯性。参考范围为**样例矩形**，正式嘉兴边界待交接。输入是原始 30 角秒栅格的相对适宜度，基准时期随来源说明核实。源数据从外部目录只读打开。数据版本由所读窗口的数值、物种和网格元数据生成。

调查分数为 `100 × 达到各自阈值（含等于）的物种数 / 所选物种数`。嘉兴 39 种数据包使用逐物种 maxSSS 阈值。通用样例使用窗口 P90。只有所有目标物种预测均有效、分数为正且未排除的网格才进入推荐清单。

同分时默认按原始网格编号选点。选择「优先分散选点」后，先取最高分中编号最小的网格，再从剩余最高分网格中选取距已有点集最远的一格。距离相同按编号决定。

比较两种规则时，先生成 A，复制条件为 B，只改同分方式，再生成 B。比较页显示原始推荐的空间重合、最近邻球面距离中位数及 demo 推算格数。距离按格心计算，单点方案的间距显示为「—」。确认后导出的 `tieBreak` 与 `originalMedianNearestKm` 记录生成规则与原始推荐的间距。

A grid’s survey score is the percentage of selected species that meet their suitability thresholds. Only cells with valid predictions for every selected species can enter the shortlist.

Set `tieBreak` to `cell_id` (default) or `spatial_spread`. Both prioritise higher scores. Spatial spread starts at the lowest ID among the highest scores, then resolves ties by maximising great-circle distance to the nearest previously selected site. Copy plan A to B to compare tie-breaks at the same survey limit.

## API 和实现入口

在「调查清单」中新增、移除或替换网格后，可对照原始推荐与当前清单的物种覆盖、最近邻距离中位数和 demo 推算格数。展开逐物种明细可查看各物种在几格内达到阈值。少于两个地点时，间距显示为「—」。确认后 JSON 和 GeoJSON 的 `context.shortlistComparison` 以及 CSV 的 `original*` / `final*` 列保存同一组指标。

The shortlist shows the original and edited lists side by side. Expand the species breakdown to inspect the number of cells meeting each selected species’ suitability cutoff. Confirm the list to include the same measures in the CSV, GeoJSON or JSON export.

- `GET /api/catalog` 返回物种、窗口、数据版本和各物种门槛。
- `GET /api/layers/{species_id}.png` 返回原生网格着色图。
- `POST /api/runs` 以 `speciesIds`、`k`、`excludedCellIds` 计算方案。
- `POST /api/shortlist/comparison` 使用当前数据、阈值和两份网格编号清单计算调整前后指标。
- `POST /api/cells/{row}/{col}` 按同样条件查询网格证据。
- `src/App.tsx` 连接页面状态与接口，`src/MapView.tsx` 负责地图交互。
- `backend/datasets.py` 读取源清单、导入和验证本地发布包。`backend/sample.py` 从发布包生成门槛和图层，`backend/policy.py` 计算资格、分数与推荐。
- `data-audit/` 保存 M0 数据检查脚本、报告和栅格预览。

下一阶段接入正式边界、发布清单、逐物种未来成员预测、39 种总览和评价表，再扩展未来规则与权重比较。当前界面标记了未来数据的可用状态。
