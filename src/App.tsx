import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { cellAtIndex, request } from './api'
import type { Catalog, Cell, Detail, Input, Run } from './api'
import { MapView } from './MapView'
import { exportDecision } from './export'
import type { Adjustment, Decision, WorkItem } from './export'

type Slot = 'A' | 'B'
type Tab = 'results' | 'ties' | 'detail' | 'compare'
type Scenario = {
  draft: Input
  run: Run | null
  worklist: WorkItem[]
  adjustments: Adjustment[]
  confirmedAt: string | null
}

const blank = (draft: Input): Scenario => ({
  draft, run: null, worklist: [], adjustments: [], confirmedAt: null,
})
const currentRun = (scenario: Scenario | undefined) =>
  scenario?.run && JSON.stringify(scenario.draft) === JSON.stringify(scenario.run.input) ? scenario.run : null
const storeKey = 'invaplan.confirmed.baseline-v1'
const num = new Intl.NumberFormat('zh-CN')
const decimal = (value: number | null | undefined) => value == null ? '未评估' : value.toFixed(3)
const shortId = ({ row, col }: Cell) => `R${row} · C${col}`

function Icon({ name, size = 17 }: { name: 'map' | 'leaf' | 'layers' | 'compare' | 'pin' | 'download' | 'check' | 'arrow'; size?: number }) {
  const paths = {
    map: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/></>,
    leaf: <><path d="M5 19c2-5 5-8 10-11"/><path d="M7 16C1 10 10 4 21 3c-1 12-6 19-14 13Z"/></>,
    layers: <><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></>,
    compare: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16m-4-9-2 2 2 2m8-6 2 2-2 2"/></>,
    pin: <><path d="M19 10c0 5-7 10-7 10s-7-5-7-10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.5"/></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    arrow: <path d="M4 12h15m-6-6 6 6-6 6"/>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [startupError, setStartupError] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    request<Catalog>('/api/catalog', undefined, abort.signal).then(setCatalog).catch(error => {
      if (!abort.signal.aborted) setStartupError(error.message)
    })
    return () => abort.abort()
  }, [])
  if (!catalog) {
    return <div className="splash"><Brand /><h1>沿海巡查决策工作台</h1>
      <p>{startupError ? `样例数据加载失败。 ${startupError}` : '正在读取本地样例数据…'}</p>
      {startupError && <button className="primary" onClick={() => location.reload()}>重新连接</button>}
    </div>
  }
  return <Workspace catalog={catalog} />
}

function Brand() {
  return <div className="brand"><span className="brand-symbol"><Icon name="leaf" size={21}/></span>
    <span>InvaPlan <small>COASTAL FIELD DESK</small></span></div>
}

function Workspace({ catalog }: { catalog: Catalog }) {
  const [slots, setSlots] = useState<Partial<Record<Slot, Scenario>>>(() => {
    const initial = { A: blank({ speciesIds: catalog.species.map(item => item.id), k: 10, excludedCellIds: [] }) }
    try {
      const stored = JSON.parse(localStorage.getItem(storeKey) ?? 'null') as Partial<Record<Slot, Scenario>> | null
      const compatible = (scenario?: Scenario) => scenario?.run?.dataVersion === catalog.dataVersion
        && scenario?.run?.policyVersion === catalog.policyVersion && !!scenario.confirmedAt
      if (compatible(stored?.A)) initial.A = stored!.A!
      return { ...initial, ...(compatible(stored?.B) ? { B: stored!.B } : {}) }
    } catch {
      return initial
    }
  })
  const [slot, setSlot] = useState<Slot>('A')
  const [tab, setTab] = useState<Tab>('results')
  const [layer, setLayer] = useState(catalog.species.find(s => s.id === 'PROD0017')?.id ?? catalog.species[0].id)
  const [cell, setCell] = useState<Pick<Cell, 'row' | 'col'> | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [locate, setLocate] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [adjust, setAdjust] = useState<{ action: Adjustment['action']; item: WorkItem } | null>(null)
  const [reason, setReason] = useState('')
  const [replaceId, setReplaceId] = useState('')
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  const scenario = slots[slot]!
  const { draft, worklist } = scenario
  const run = currentRun(scenario)
  const a = currentRun(slots.A)
  const b = currentRun(slots.B)
  const isComparing = tab === 'compare' && !!a && !!b
  const byName = new Map(catalog.species.map(s => [s.id, s.name]))
  const cutoff = run?.recommendations.at(-1)?.score
  const tiedCandidates = useMemo(() => {
    const candidates = new Map<string, Cell>()
    if (!run || cutoff === undefined) return candidates
    const selectedIds = new Set(worklist.map(item => item.id))
    run.scores.forEach((score, index) => {
      if (score !== cutoff) return
      const candidate = cellAtIndex(catalog.grid, index)
      if (!selectedIds.has(candidate.id)) candidates.set(candidate.id, candidate)
    })
    return candidates
  }, [run, cutoff, catalog.grid, worklist])
  const comparison = useMemo(() => {
    if (!a || !b) return { common: [], onlyA: [], onlyB: [] }
    const aIds = new Set(a.recommendations.map(c => c.id))
    const bIds = new Set(b.recommendations.map(c => c.id))
    return {
      common: a.recommendations.filter(c => bIds.has(c.id)),
      onlyA: a.recommendations.filter(c => !bIds.has(c.id)),
      onlyB: b.recommendations.filter(c => !aIds.has(c.id)),
    }
  }, [a, b])

  useEffect(() => {
    if (!cell || draft.speciesIds.length === 0) {
      setDetail(null)
      setDetailError('')
      return
    }
    const abort = new AbortController()
    setDetail(null)
    setDetailError('')
    request<Detail>(`/api/cells/${cell.row}/${cell.col}`, draft, abort.signal)
      .then(value => { if (!abort.signal.aborted) setDetail(value) })
      .catch(cause => { if (!abort.signal.aborted) setDetailError(cause.message) })
    return () => abort.abort()
  }, [cell, draft])

  function updateScenario(update: (previous: Scenario) => Scenario, key = slot) {
    setSlots(previous => ({ ...previous, [key]: update(previous[key]!) }))
  }
  function changeDraft(input: Input) {
    updateScenario(previous => ({ ...previous, draft: input, confirmedAt: null }))
    setError('')
    setNotice('')
  }
  function toggleSpecies(id: string) {
    const selected = new Set(draft.speciesIds)
    if (selected.has(id)) selected.delete(id)
    else selected.add(id)
    changeDraft({ ...draft, speciesIds: catalog.species.filter(s => selected.has(s.id)).map(s => s.id) })
  }
  async function generate(event: FormEvent) {
    event.preventDefault()
    if (!draft.speciesIds.length || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await request<Run>('/api/runs', draft)
      updateScenario(previous => ({ ...blank(previous.draft), run: response, worklist: response.recommendations }), slot)
      setTab('results')
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }
  function switchSlot(key: Slot) {
    setSlot(key)
    setTab('results')
    setError('')
    setNotice('')
  }
  function copyForComparison() {
    if (slots.B) {
      switchSlot('B')
      return
    }
    setSlots(previous => ({ ...previous, B: blank(previous.A!.draft) }))
    switchSlot('B')
    setNotice('已复制方案 A 的条件。调整巡查上限，再生成方案 B。')
  }
  function pickCell(next: Pick<Cell, 'row' | 'col'>, shouldLocate = false) {
    setCell(next)
    if (shouldLocate) setLocate(value => value + 1)
    setTab('detail')
  }
  function openAdjustment(action: Adjustment['action'], item: WorkItem) {
    setAdjust({ action, item })
    setReason('')
    setReplaceId('')
  }
  function applyAdjustment(event: FormEvent) {
    event.preventDefault()
    if (!run || busy || !adjust || !reason.trim()) return
    const details = { cell: adjust.item, reason: reason.trim(), timestamp: new Date().toISOString() }
    let next: WorkItem[]
    let record: Adjustment
    if (adjust.action === 'replace') {
      const outgoing = worklist.find(item => item.id === replaceId && item.score === cutoff)
      if (!outgoing || !tiedCandidates.has(adjust.item.id) || adjust.item.score !== cutoff || worklist.length > draft.k) return
      next = worklist.map(item => item.id === outgoing.id ? adjust.item : item)
      record = { ...details, action: 'replace', replacedCell: outgoing }
      setTab('results')
    } else {
      if (adjust.action === 'add' && (worklist.length >= draft.k || worklist.some(item => item.id === adjust.item.id))) return
      next = adjust.action === 'add'
        ? [...worklist, adjust.item] : worklist.filter(item => item.id !== adjust.item.id)
      record = { ...details, action: adjust.action }
    }
    updateScenario(previous => ({
      ...previous, worklist: next, confirmedAt: null, adjustments: [...previous.adjustments, record],
    }))
    setAdjust(null)
    setNotice(record.action === 'replace'
      ? `已用 ${shortId(record.cell)} 替换 ${shortId(record.replacedCell)}。清单 ${next.length} / ${draft.k}。`
      : '已记录人工调整。确认后可导出最终清单。')
  }
  function confirm() {
    if (!run || worklist.length === 0 || worklist.length > draft.k) return
    const updated = { ...scenario, confirmedAt: new Date().toISOString() }
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey) ?? '{}')
      localStorage.setItem(storeKey, JSON.stringify({ ...saved, [slot]: updated }))
      updateScenario(() => updated)
      setNotice(`方案 ${slot} 已确认并保存在此浏览器。`)
    } catch {
      setError('浏览器无法保存方案。请检查本地存储设置。')
    }
  }
  function download(format: 'csv' | 'geojson' | 'json') {
    if (!run || !scenario.confirmedAt || !worklist.length) return
    const decision: Decision = { run, worklist, adjustments: scenario.adjustments, confirmedAt: scenario.confirmedAt }
    exportDecision(format, decision, catalog)
  }

  const highlights = isComparing ? [
    ...comparison.common.map(item => ({ cell: item, group: 'common' as const })),
    ...comparison.onlyA.map(item => ({ cell: item, group: 'a' as const })),
    ...comparison.onlyB.map(item => ({ cell: item, group: 'b' as const })),
  ] : (run ? [
    ...Array.from(tiedCandidates.values(), candidate => ({ cell: candidate, group: 'tie' as const })),
    ...worklist.map(item => ({ cell: item, group: 'single' as const })),
  ] : [])
  const detailInList = detail && worklist.some(item => item.id === detail.cell.id)
  const detailIsTie = detail && tiedCandidates.has(detail.cell.id)
  const replaceable = worklist.filter(item => item.score === cutoff)
  const stale = !!scenario.run && !run
  const displayLayer = layer === 'priority' && !run ? catalog.species[0].id : layer
  const selectedCell = cell ? cellAtIndex(catalog.grid,
    (cell.row - catalog.grid.row0) * catalog.grid.width + cell.col - catalog.grid.col0) : null

  return <>
    <header className="topbar"><div className="top-inner">
      <Brand />
      <div className="top-right"><span className="live"><i /> 真实样例数据</span>
        <button className="quiet" onClick={() => setEvidenceOpen(true)}>数据说明</button>
      </div>
    </div></header>
    <main className="workspace">
      <section className="page-head">
        <div><div className="eyebrow"><span className="line" /> SPACE · EVIDENCE · DECISION</div>
          <h1>沿海巡查决策工作台</h1>
          <p>从多物种预测证据出发，制定、比较并记录巡查方案。</p>
        </div>
        <div className="region"><Icon name="pin" size={19}/><div><strong>{catalog.name}</strong><small>样例矩形 · 原生网格 30″</small></div></div>
      </section>
      <div className="board">
        <form className="panel controls" onSubmit={generate}>
          <div className="panel-head"><span className="step">01</span><div><h2>调查条件</h2><p>定义本轮任务</p></div></div>
          <fieldset disabled={busy}>
            <section className="section-block">
              <label className="field-label">当前方案</label>
              <div className="segments">
                <button type="button" className={slot === 'A' ? 'active' : ''} onClick={() => switchSlot('A')}>方案 A</button>
                <button type="button" disabled={!slots.B} className={slot === 'B' ? 'active' : ''} onClick={() => switchSlot('B')}>方案 B</button>
              </div>
            </section>
            <section className="section-block">
              <div className="field-heading"><label className="field-label" htmlFor="capacity">巡查网格上限</label><span className="count">{draft.k} 个</span></div>
              <input id="capacity" type="range" min="1" max={catalog.maxK} value={draft.k}
                onChange={e => changeDraft({ ...draft, k: Number(e.target.value) })} />
              <div className="range-labels"><span>1</span><span>{catalog.maxK}</span></div>
            </section>
            <section className="section-block">
              <div className="field-heading"><span className="field-label">重点物种</span><button className="text-button" type="button"
                onClick={() => changeDraft({ ...draft, speciesIds: draft.speciesIds.length === catalog.species.length ? [] : catalog.species.map(s => s.id) })}>
                {draft.speciesIds.length === catalog.species.length ? '清空' : '全选'}
              </button></div>
              <div className="species-list">{catalog.species.map(item => <label className="species-option" key={item.id}>
                <input type="checkbox" checked={draft.speciesIds.includes(item.id)} onChange={() => toggleSpecies(item.id)} />
                <span>{item.name}<small>{item.latin}</small></span>{item.exploratory && <span className="tag warm" title="该物种来源保留探索性标记">探索</span>}
              </label>)}</div>
            </section>
            <section className="section-block mode"><div className="field-heading"><span className="field-label">分析模式</span><span className="tag green">基准期</span></div>
              <p>未来联合方案待接入成员预测。</p>
            </section>
            <button type="submit" className="primary run-button" disabled={!draft.speciesIds.length || busy}>{busy ? '正在计算…' : '生成调查方案'} <Icon name="arrow"/></button>
          </fieldset>
        </form>

        <section className="panel map-panel">
          <div className="panel-head map-head"><span className="step">02</span><div><h2>证据地图</h2><p>点击网格查看物种与推荐依据</p></div>
            <label className="layer-select"><Icon name="layers"/><select aria-label="地图图层" value={displayLayer} onChange={event => setLayer(event.target.value)}>
              {catalog.species.map(item => <option key={item.id} value={item.id}>{item.name} · 适宜度</option>)}
              <option value="priority" disabled={!run}>方案优先分</option>
            </select></label>
          </div>
          <div className="map-wrap">
            <MapView catalog={catalog} layer={displayLayer} run={run}
              highlights={highlights} selected={selectedCell} locateRequest={locate}
              onPick={(row, col) => pickCell({ row, col })} />
            <div className="map-caption"><Icon name="map" size={14}/> WGS 84 · 样例矩形</div>
          </div>
          <div className="map-legend">
            <span>{displayLayer === 'priority' ? '优先分' : '相对适宜度'}</span><span className="ramp" /><span>低 → 高</span>
            <span><i className="swatch masked" /> {displayLayer === 'priority' ? '未评分' : '未评估'}</span>
            {isComparing ? <><span><i className="swatch common"/> 共同</span><span><i className="swatch picked"/> 仅 A</span><span><i className="swatch b-only"/> 仅 B</span></>
              : <><span><i className="swatch picked"/> 工作清单</span>
                {tiedCandidates.size > 0 && <span><i className="swatch tied"/> 同分候选</span>}</>}
          </div>
          <div className="map-footer"><span>研究样例 · 原始预测 30 角秒</span><span>{num.format(catalog.grid.width * catalog.grid.height)} 个原生网格</span></div>
        </section>

        <section className="panel results-panel">
          <div className="panel-head"><span className="step">03</span><div><h2>方案与依据</h2><p>复核选择，再确认清单</p></div><span className="slot-mark">{slot}</span></div>
          <div className="result-tabs" role="group" aria-label="结果视图">
            <button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}>工作清单</button>
            <button className={tab === 'ties' ? 'active' : ''} disabled={!run || tiedCandidates.size === 0} onClick={() => setTab('ties')}>同分候选</button>
            <button className={tab === 'detail' ? 'active' : ''} onClick={() => setTab('detail')}>网格详情</button>
            <button className={tab === 'compare' ? 'active' : ''} onClick={() => setTab('compare')}>比较</button>
          </div>
          <div className="results-body">
            {error && <p className="message error" role="alert">{error}</p>}
            {notice && <p className="message success" role="status">{notice}</p>}
            {stale && <p className="message warning" role="status">条件已改变，请重新生成方案 {slot}。</p>}
            {tab === 'results' && (run ? <>
              <div className="metrics"><div><strong>{worklist.length}</strong><span>清单 / {draft.k} 上限</span></div>
                <div><strong>{num.format(run.rankedCount)}</strong><span>证据完整</span></div>
                <div><strong>{num.format(run.incompleteCount)}</strong><span>证据不足</span></div></div>
              <p className="micro">系统推荐 {run.recommendations.length} 个 · 人工调整 {scenario.adjustments.length} 次{run.excludedCount ? ` · 排除 ${run.excludedCount} 个` : ''}</p>
              {tiedCandidates.size > 0 && <button className="tie-summary" onClick={() => setTab('ties')}>
                <i className="swatch tied"/><span>查看 {num.format(tiedCandidates.size)} 个同分候选</span><Icon name="arrow" size={14}/>
              </button>}
              {run.recommendations.length < draft.k && <p className="message warning">符合正分条件的网格不足 {draft.k} 个。</p>}
              <div className="recommendations">{worklist.map((item, index) => <article className="result-item" key={item.id}>
                <button className="cell-link" onClick={() => pickCell(item, true)}><span className="rank">{index + 1}</span><span>
                  <strong>{shortId(item)}</strong><small>{item.c ? item.focusIds.map(id => byName.get(id)).join('、') : '人工核查'}</small></span></button>
                <div className="item-right"><strong>{item.score?.toFixed(1) ?? '—'}</strong>
                  <button className="text-button subtle" onClick={() => openAdjustment('remove', item)} aria-label={`移出 ${shortId(item)}`}>移出</button></div>
              </article>)}</div>
              {slot === 'A' && <button className="secondary full" onClick={copyForComparison}><Icon name="compare"/>{slots.B ? '编辑方案 B' : '复制条件为方案 B'}</button>}
            </> : <div className="empty"><span className="empty-icon"><Icon name="map" size={24}/></span><h3>准备一份巡查方案</h3>
              <p>选择物种和巡查上限，再生成推荐。也可以先点击地图查看原始预测。</p></div>)}
            {tab === 'ties' && (tiedCandidates.size > 0 ? <>
              <div className="tie-heading"><i className="swatch tied"/><h3>{num.format(tiedCandidates.size)} 个同分候选</h3></div>
              <p className="micro">蓝色网格与原始推荐末位同为 {cutoff?.toFixed(1)} 分。选择地图或下方列表中的网格，可用它替换清单中的同分项。</p>
              <div className="recommendations tie-list">{Array.from(tiedCandidates.values(), candidate =>
                <button key={candidate.id} className="difference" onClick={() => pickCell(candidate, true)}>
                  <span>{shortId(candidate)}</span><span>{candidate.longitude.toFixed(4)}°E，{candidate.latitude.toFixed(4)}°N</span>
                </button>)}</div>
            </> : <p className="micro">当前方案没有可选的同分网格。</p>)}
            {tab === 'detail' && (detail ? <>
              <div className="detail-title"><div><h3>{shortId(detail.cell)}</h3>
                <p>{detail.cell.longitude.toFixed(4)}°E，{detail.cell.latitude.toFixed(4)}°N</p></div>
                <span className={detail.score == null ? 'tag warm' : 'tag green'}>{detail.excluded ? '已排除' : detail.score == null ? '证据不足' : `${detail.score.toFixed(1)} 分`}</span></div>
              <div className="explanation">{detail.excluded ? '已排除此网格。'
                : detail.missingIds.length ? `${detail.missingIds.map(id => byName.get(id)).join('、')}缺少有效预测，暂无法评分。`
                : `${detail.c} / ${draft.speciesIds.length} 种达到各自门槛。优先分 = 100 × ${detail.c} / ${draft.speciesIds.length}。`}</div>
              {a && b && <div className="micro">方案 A {a.scores[(detail.cell.row - catalog.grid.row0) * catalog.grid.width + detail.cell.col - catalog.grid.col0]?.toFixed(1) ?? '—'} 分
                · 方案 B {b.scores[(detail.cell.row - catalog.grid.row0) * catalog.grid.width + detail.cell.col - catalog.grid.col0]?.toFixed(1) ?? '—'} 分</div>}
              <table className="detail-table"><thead><tr><th>物种</th><th>预测</th><th>门槛</th><th>状态</th></tr></thead>
                <tbody>{catalog.species.map(s => <tr key={s.id}>
                  <td>{s.name}</td><td>{decimal(detail.values[s.id])}</td><td>{decimal(catalog.thresholds[s.id].value)}</td>
                  <td>{!draft.speciesIds.includes(s.id) ? '未选' : detail.values[s.id] == null ? '缺失' : detail.focusIds.includes(s.id) ? <span className="tag green">关注</span> : '未触发'}</td>
                </tr>)}</tbody></table>
              <p className="micro">门槛固定为各物种在样例窗口内的第 90 百分位。</p>
              {detailIsTie && <p className="message tie-message"><i className="swatch tied"/> 同分候选 · 可以替换清单中的同分网格</p>}
              <div className="detail-actions">
                {detailIsTie && replaceable.length > 0 && <button className="primary" disabled={busy}
                  onClick={() => openAdjustment('replace', { ...detail.cell, score: detail.score, c: detail.c, focusIds: detail.focusIds })}>
                  替换清单网格
                </button>}
                <button className="secondary" disabled={busy} onClick={() => changeDraft({ ...draft, excludedCellIds: detail.excluded
                  ? draft.excludedCellIds.filter(id => id !== detail.cell.id) : [...draft.excludedCellIds, detail.cell.id] })}>
                  {detail.excluded ? '恢复候选' : '排除候选'}
                </button>
                {run && (detailInList ? <button className="secondary" onClick={() => openAdjustment('remove', worklist.find(item => item.id === detail.cell.id)!)}>移出清单</button>
                  : <button className="secondary" disabled={worklist.length >= draft.k || detail.score == null || detail.excluded}
                    onClick={() => openAdjustment('add', { ...detail.cell, score: detail.score, c: detail.c, focusIds: detail.focusIds })}>加入清单</button>)}
              </div>
            </> : <div className="empty"><span className="empty-icon"><Icon name="pin" size={24}/></span><h3>{detailError || (cell && draft.speciesIds.length ? '读取网格…' : '选择一个网格')}</h3><p>在地图或工作清单中选择位置。</p></div>)}
            {tab === 'compare' && (a && b ? <>
              <div className="metrics compare-metrics"><div><strong>{comparison.common.length}</strong><span>共同入选</span></div>
                <div><strong>{comparison.onlyA.length}</strong><span>仅 A</span></div><div><strong>{comparison.onlyB.length}</strong><span>仅 B</span></div></div>
              <p className="micro">比较系统原始推荐。A 上限 {a.input.k} · B 上限 {b.input.k}。</p>
              <p className="micro">A 选中 {a.input.speciesIds.length} 个物种 · B 选中 {b.input.speciesIds.length} 个物种。</p>
              {[...comparison.onlyA.map(item => ({ ...item, label: '仅 A' })), ...comparison.onlyB.map(item => ({ ...item, label: '仅 B' }))].map(item =>
                <button key={item.id} className="difference" onClick={() => pickCell(item, true)}><span>{shortId(item)}</span><span>{item.label} · {item.score.toFixed(1)} 分</span></button>)}
              {comparison.onlyA.length + comparison.onlyB.length === 0 && <p className="message success">两套方案推荐了相同的网格。</p>}
            </> : <div className="empty"><span className="empty-icon"><Icon name="compare" size={24}/></span><h3>比较两套建议</h3>
              <p>生成方案 A 后复制条件，调整巡查上限或关注物种，再生成方案 B。</p>
              {a && <button className="secondary" onClick={copyForComparison}>{slots.B ? '编辑方案 B' : '复制为方案 B'}</button>}</div>)}
          </div>
          <div className="result-footer">
            <button className="secondary full" disabled={!run || !worklist.length || !!scenario.confirmedAt} onClick={confirm}>
              <Icon name="check"/>{scenario.confirmedAt && run ? '方案已确认' : '确认方案'}</button>
            <div className="export-row">{(['csv', 'geojson', 'json'] as const).map(format => <button key={format}
              disabled={!run || !scenario.confirmedAt || !worklist.length} onClick={() => download(format)}><Icon name="download" size={13}/>{format === 'geojson' ? 'GeoJSON' : format.toUpperCase()}</button>)}</div>
          </div>
        </section>
      </div>
      <footer className="workspace-footer"><span>InvaPlan · 多物种巡查优先区原型</span><span>{catalog.policyVersion} · {catalog.dataVersion}</span></footer>
    </main>
    {adjust && <div className="scrim" onClick={() => setAdjust(null)}><form className="dialog" onClick={event => event.stopPropagation()} onSubmit={applyAdjustment} role="dialog" aria-modal="true" aria-labelledby="adjust-title">
      <h2 id="adjust-title">{adjust.action === 'replace' ? '换入候选' : adjust.action === 'add' ? '人工加入' : '移出清单'} {shortId(adjust.item)}</h2>
      <p>{adjust.action === 'replace'
        ? `选择换出的同分网格。替换后仍为 ${worklist.length} / ${draft.k}，原始推荐会随方案保留。`
        : '记录此次调整的原因，原始推荐会随方案保留。'}</p>
      {adjust.action === 'replace' && <>
        <label htmlFor="replace-cell">换出网格</label>
        <select id="replace-cell" required autoFocus value={replaceId} onChange={e => setReplaceId(e.target.value)}>
          <option value="">选择清单中的同分网格</option>
          {replaceable.map(item => <option key={item.id} value={item.id}>{shortId(item)} · {item.score?.toFixed(1)} 分</option>)}
        </select>
      </>}
      <label htmlFor="adjust-reason">{adjust.action === 'replace' ? '替换理由' : '调整理由'}</label>
      <textarea id="adjust-reason" value={reason} maxLength={280} required autoFocus={adjust.action !== 'replace'} onChange={e => setReason(e.target.value)} />
      <div className="dialog-actions"><button className="secondary" type="button" onClick={() => setAdjust(null)}>取消</button>
        <button className="primary" disabled={!reason.trim() || (adjust.action === 'replace' && !replaceId)}>
          {adjust.action === 'replace' ? '确认替换' : '记录调整'}
        </button></div>
    </form></div>}
    {evidenceOpen && <div className="scrim" onClick={() => setEvidenceOpen(false)}><section className="dialog" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <h2 id="evidence-title">数据与决策规则</h2>
      <dl><dt>空间范围</dt><dd>嘉兴周边的样例矩形。正式行政边界和陆域定义待交接。</dd>
        <dt>模型输出</dt><dd>空间嵌套 SDM 集成相对适宜度指数。地图数值反映相对适宜度，发生与危害需要另行核实。</dd>
        <dt>基准时期</dt><dd>{catalog.baselinePeriod ?? '来源时期待核实'} · 原生分辨率 30 角秒。</dd>
        <dt>选区方法</dt><dd>按各物种在样例矩形内的第 90 百分位判定高值。所选物种全部有值时，以达到门槛的物种占比计算优先分。</dd>
        <dt>证据缺口</dt><dd>灰色代表该图层的未评估像元。所选物种任一缺失时，网格归入证据不足。</dd>
        <dt>数据版本</dt><dd>{catalog.dataVersion} · {catalog.policyVersion}</dd></dl>
      <button className="primary full" onClick={() => setEvidenceOpen(false)}>返回工作台</button>
    </section></div>}
  </>
}
