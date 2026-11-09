# InvaPlan 沿海巡查决策工作台

本地交互原型。React 和 Leaflet 显示九种入侵植物的样例栅格，FastAPI 读取数据、汇总证据并计算巡查建议。

## 启动

在本目录执行 `pnpm install --store-dir .pnpm-store` 和 `pnpm build`。首次配置时建立项目内的 Python 环境并安装依赖。

```powershell
uv venv .venv --python 3.14 --cache-dir .uv-cache
uv pip install --python '.\.venv\Scripts\python.exe' --cache-dir .uv-cache -r requirements.txt
```

在 PowerShell 启动，路径指向含九张 TIFF 的目录。

```powershell
.\Start-Demo.ps1 -RasterDir 'E:\Project\Website\InvaPlanDataSample\current_S2_30s'
```

浏览器打开 [本地工作台](http://127.0.0.1:8765)。按 Ctrl+C 停止服务。需要实时修改界面时，保持后端运行，在另一个终端运行 `pnpm dev`，通过 Vite 的代理使用同一组 API。

## 演示流程

默认九物种全选。生成方案 A，点击清单项查看网格证据。复制为方案 B，改变数量上限，再生成一次。比较页会在同一张地图上用三种颜色标出交集、A 独有和 B 独有。工作清单支持带理由的移出和人工加入。确认后可导出 CSV、GeoJSON 或含完整决定的 JSON。已确认方案保存在当前浏览器，同一数据和规则版本下刷新可继续查看。

## 数据与规则

首版读取嘉兴周边的一个矩形窗口（东经 120.45° 至 121.25°、北纬 30.40° 至 31.05°），用原始网格行列编号保留可追溯性。参考范围为**样例矩形**，正式嘉兴边界待交接。输入是原始 30 角秒栅格的相对适宜度，基准时期随来源说明核实。源数据从外部目录只读打开。数据版本由所读窗口的数值、物种和网格元数据生成。

`pilot_baseline_p90_v1` 对每个物种在样例窗口内的有效值计算线性插值 P90，达到门槛（含等于）计一次关注。所选物种均有有效值时，优先分为 `100 × 关注物种数 / 所选物种数`。所选物种缺失的网格标为证据不足。按分数降序、原始网格编号升序确定前 K 个正分候选。人工调整单独记录，清单遵守 K 上限。结果适用于演示巡查判断，科学解释和真实管理应用由研究者核定。

## API 和实现入口

- `GET /api/catalog` 返回物种、窗口、数据版本和各物种门槛。
- `GET /api/layers/{species_id}.png` 返回原生网格着色图。
- `POST /api/runs` 以 `speciesIds`、`k`、`excludedCellIds` 计算方案。
- `POST /api/cells/{row}/{col}` 按同样条件查询网格证据。
- `src/App.tsx` 连接页面状态与接口，`src/MapView.tsx` 负责地图交互。
- `backend/sample.py` 读取窗口和固定门槛，`backend/policy.py` 计算资格、分数与推荐。
- `data-audit/` 保存 M0 数据检查脚本、报告和栅格预览。

下一阶段接入正式边界、发布清单、逐物种未来成员预测、39 种总览和评价表，再扩展未来规则与权重比较。当前界面标记了未来数据的可用状态。
