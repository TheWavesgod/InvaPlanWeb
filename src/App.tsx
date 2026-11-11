import { useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { cellAtIndex, request } from './api'
import type { Catalog, Cell, Detail, Input, Run } from './api'
import { MapView } from './MapView'
import { exportDecision } from './export'
import type { Adjustment, Decision, WorkItem } from './export'
import { createTranslator, languageTags, localisedRegionName, requestFailure } from './i18n'
import type { Language, Message, Translate } from './i18n'

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
const languageKey = 'invaplan.language'

function getInitialLanguage(): Language {
  try { return localStorage.getItem(languageKey) === 'en' ? 'en' : 'zh' }
  catch { return 'zh' }
}
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
  const [startupError, setStartupError] = useState<Message | null>(null)
  const [language, setLanguage] = useState<Language>(getInitialLanguage)
  const t = useMemo(() => createTranslator(language), [language])

  useEffect(() => {
    document.documentElement.lang = languageTags[language]
    document.title = t('pageTitle')
    document.querySelector('meta[name="description"]')?.setAttribute('content', t('pageDescription'))
    try { localStorage.setItem(languageKey, language) }
    catch { /* The switch works for this visit even if browser storage is unavailable. */ }
  }, [language, t])

  const languageControl = <button className="quiet language-toggle" onClick={() => setLanguage(value => value === 'zh' ? 'en' : 'zh')}
    aria-label={t('switchLanguage')} lang={language === 'zh' ? 'en' : 'zh-CN'}>
    {language === 'zh' ? 'EN' : '中文'}
  </button>
  useEffect(() => {
    const abort = new AbortController()
    request<Catalog>('/api/catalog', undefined, abort.signal).then(setCatalog).catch(error => {
      if (!abort.signal.aborted) setStartupError(requestFailure(error))
    })
    return () => abort.abort()
  }, [])
  if (!catalog) {
    return <div className="splash"><Brand t={t}/>{languageControl}<h1>{t('title')}</h1>
      <p>{startupError ? t('startupFailed', { error: t(startupError) }) : t('loadingData')}</p>
      {startupError && <button className="primary" onClick={() => location.reload()}>{t('reconnect')}</button>}
    </div>
  }
  return <Workspace catalog={catalog} language={language} t={t} languageControl={languageControl} />
}

function Brand({ t }: { t: Translate }) {
  return <div className="brand"><span className="brand-symbol"><Icon name="leaf" size={21}/></span>
    <span>InvaPlan <small>{t('brandSubtitle')}</small></span></div>
}

function Workspace({ catalog, language, t, languageControl }: {
  catalog: Catalog
  language: Language
  t: Translate
  languageControl: ReactNode
}) {
  const num = new Intl.NumberFormat(languageTags[language])
  const list = new Intl.ListFormat(languageTags[language])
  const regionName = language === 'en' && catalog.nameEn ? catalog.nameEn : localisedRegionName(catalog.name, language)
  const [west, south, east, north] = catalog.grid.bounds
  const angularFormat = new Intl.NumberFormat(languageTags[language], { maximumFractionDigits: 3 })
  const horizontal = angularFormat.format((east - west) / catalog.grid.width * 3600)
  const vertical = angularFormat.format((north - south) / catalog.grid.height * 3600)
  const resolution = horizontal === vertical ? horizontal : `${horizontal} × ${vertical}`
  const decimal = (value: number | null | undefined) => value == null ? t('notAssessed') : value.toFixed(3)
  const [slots, setSlots] = useState<Partial<Record<Slot, Scenario>>>(() => {
    const initial = { A: blank({ speciesIds: catalog.defaultSpeciesIds ?? catalog.species.map(item => item.id), k: 10, excludedCellIds: [] }) }
    try {
      const stored = JSON.parse(localStorage.getItem(storeKey) ?? 'null') as Partial<Record<Slot, Scenario>> | null
      const compatible = (scenario?: Scenario) => scenario?.run?.dataVersion === catalog.dataVersion
        && scenario?.run?.policyVersion === catalog.policyVersion
        && (!scenario.run.releaseId || scenario.run.releaseId === catalog.releaseId) && !!scenario.confirmedAt
      if (compatible(stored?.A)) initial.A = stored!.A!
      return { ...initial, ...(compatible(stored?.B) ? { B: stored!.B } : {}) }
    } catch {
      return initial
    }
  })
  const [slot, setSlot] = useState<Slot>('A')
  const [tab, setTab] = useState<Tab>('results')
  const [layer, setLayer] = useState(catalog.defaultLayerId ?? catalog.species[0].id)
  const [cell, setCell] = useState<Pick<Cell, 'row' | 'col'> | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailError, setDetailError] = useState<Message | null>(null)
  const [locate, setLocate] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Message | null>(null)
  const [notice, setNotice] = useState<Message | null>(null)
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
  const byName = new Map(catalog.species.map(s => [s.id, language === 'en' ? (s.nameEn ?? s.latin) : s.name]))
  const speciesNames = (ids: string[]) => list.format(ids.map(id => byName.get(id) ?? id))
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
      setDetailError(null)
      return
    }
    const abort = new AbortController()
    setDetail(null)
    setDetailError(null)
    request<Detail>(`/api/cells/${cell.row}/${cell.col}`, draft, abort.signal)
      .then(value => { if (!abort.signal.aborted) setDetail(value) })
      .catch(cause => { if (!abort.signal.aborted) setDetailError(requestFailure(cause)) })
    return () => abort.abort()
  }, [cell, draft])

  function updateScenario(update: (previous: Scenario) => Scenario, key = slot) {
    setSlots(previous => ({ ...previous, [key]: update(previous[key]!) }))
  }
  function changeDraft(input: Input) {
    updateScenario(previous => ({ ...previous, draft: input, confirmedAt: null }))
    setError(null)
    setNotice(null)
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
    setError(null)
    setNotice(null)
    try {
      const response = await request<Run>('/api/runs', draft)
      updateScenario(previous => ({ ...blank(previous.draft), run: response, worklist: response.recommendations }), slot)
      setTab('results')
    } catch (cause) {
      setError(requestFailure(cause))
    } finally {
      setBusy(false)
    }
  }
  function switchSlot(key: Slot) {
    setSlot(key)
    setTab('results')
    setError(null)
    setNotice(null)
  }
  function copyForComparison() {
    if (slots.B) {
      switchSlot('B')
      return
    }
    setSlots(previous => ({ ...previous, B: blank(previous.A!.draft) }))
    switchSlot('B')
    setNotice({ key: 'copied' })
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
      ? { key: 'swapped', values: { incoming: shortId(record.cell), outgoing: shortId(record.replacedCell), count: next.length, k: draft.k } }
      : { key: 'adjusted' })
  }
  function confirm() {
    if (!run || worklist.length === 0 || worklist.length > draft.k) return
    const updated = { ...scenario, confirmedAt: new Date().toISOString() }
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey) ?? '{}')
      localStorage.setItem(storeKey, JSON.stringify({ ...saved, [slot]: updated }))
      updateScenario(() => updated)
      setNotice({ key: 'saved', values: { slot } })
    } catch {
      setError({ key: 'storageError' })
    }
  }
  function download(format: 'csv' | 'geojson' | 'json') {
    if (!run || !scenario.confirmedAt || !worklist.length) return
    const decision: Decision = { run, worklist, adjustments: scenario.adjustments, confirmedAt: scenario.confirmedAt }
    exportDecision(format, decision, { ...catalog, name: regionName })
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
      <Brand t={t} />
      <div className="top-right">{languageControl}<span className="live"><i /> {t('realData')}</span>
        <button className="quiet" onClick={() => setEvidenceOpen(true)}>{t('dataInfo')}</button>
      </div>
    </div></header>
    <main className="workspace">
      <section className="page-head">
        <div><div className="eyebrow"><span className="line" /> {t('eyebrow')}</div>
          <h1>{t('title')}</h1>
          <p>{t('workspaceIntro')}</p>
        </div>
        <div className="region"><Icon name="pin" size={19}/><div><strong>{regionName}</strong><small>{t('regionResolution', { resolution })}</small></div></div>
      </section>
      <div className="board">
        <form className="panel controls" onSubmit={generate}>
          <div className="panel-head"><span className="step">01</span><div><h2>{t('surveyConditions')}</h2><p>{t('taskSubtitle')}</p></div></div>
          <fieldset disabled={busy}>
            <section className="section-block">
              <label className="field-label">{t('currentPlan')}</label>
              <div className="segments">
                <button type="button" className={slot === 'A' ? 'active' : ''} onClick={() => switchSlot('A')}>{t('plan', { slot: 'A' })}</button>
                <button type="button" disabled={!slots.B} className={slot === 'B' ? 'active' : ''} onClick={() => switchSlot('B')}>{t('plan', { slot: 'B' })}</button>
              </div>
            </section>
            <section className="section-block">
              <div className="field-heading"><label className="field-label" htmlFor="capacity">{t('surveyLimit')}</label><span className="count">{t('gridCount', { count: draft.k })}</span></div>
              <input id="capacity" type="range" min="1" max={catalog.maxK} value={draft.k}
                onChange={e => changeDraft({ ...draft, k: Number(e.target.value) })} />
              <div className="range-labels"><span>1</span><span>{catalog.maxK}</span></div>
            </section>
            <section className="section-block">
              <div className="field-heading"><span className="field-label">{t('speciesFocus')}</span><button className="text-button" type="button"
                onClick={() => changeDraft({ ...draft, speciesIds: draft.speciesIds.length === catalog.species.length ? [] : catalog.species.map(s => s.id) })}>
                {draft.speciesIds.length === catalog.species.length ? t('clear') : t('selectAll')}
              </button></div>
              <div className="species-list">{catalog.species.map(item => <label className="species-option" key={item.id}>
                <input type="checkbox" checked={draft.speciesIds.includes(item.id)} onChange={() => toggleSpecies(item.id)} />
                <span>{byName.get(item.id)}{language === 'zh' && <small lang="la">{item.latin}</small>}</span>{item.exploratory && <span className="tag warm" title={t('exploratoryTitle')}>{t('exploratory')}</span>}
              </label>)}</div>
            </section>
            <section className="section-block mode"><div className="field-heading"><span className="field-label">{t('analysisMode')}</span><span className="tag green">{t('baseline')}</span></div>
              <p>{t('futureUnavailable')}</p>
            </section>
            <button type="submit" className="primary run-button" disabled={!draft.speciesIds.length || busy}>{busy ? t('generating') : t('generate')} <Icon name="arrow"/></button>
          </fieldset>
        </form>

        <section className="panel map-panel">
          <div className="panel-head map-head"><span className="step">02</span><div><h2>{t('evidenceMap')}</h2><p>{t('mapSubtitle')}</p></div>
            <label className="layer-select"><Icon name="layers"/><select aria-label={t('mapLayer')} value={displayLayer} onChange={event => setLayer(event.target.value)}>
              {catalog.species.map(item => <option key={item.id} value={item.id}>{t('speciesLayer', { name: byName.get(item.id) ?? item.id })}</option>)}
              <option value="priority" disabled={!run}>{t('planPriority')}</option>
            </select></label>
          </div>
          <div className="map-wrap">
            <MapView t={t} catalog={catalog} layer={displayLayer} run={run}
              highlights={highlights} selected={selectedCell} locateRequest={locate}
              onPick={(row, col) => pickCell({ row, col })} />
            <div className="map-caption"><Icon name="map" size={14}/> WGS 84 · {t('sampleRectangle')}</div>
          </div>
          <div className="map-legend">
            <span>{displayLayer === 'priority' ? t('priority') : t('suitability')}</span><span className="ramp" /><span>{t('lowHigh')}</span>
            <span><i className="swatch masked" /> {displayLayer === 'priority' ? t('notScored') : t('notAssessed')}</span>
            {isComparing ? <><span><i className="swatch common"/> {t('common')}</span><span><i className="swatch picked"/> {t('onlyA')}</span><span><i className="swatch b-only"/> {t('onlyB')}</span></>
              : <><span><i className="swatch picked"/> {t('worklist')}</span>
                {tiedCandidates.size > 0 && <span><i className="swatch tied"/> {t('ties')}</span>}</>}
          </div>
          <div className="map-footer"><span>{t('sourceResolution', { resolution })}</span><span>{t('nativeGridCount', { count: num.format(catalog.grid.width * catalog.grid.height) })}</span></div>
        </section>

        <section className="panel results-panel">
          <div className="panel-head"><span className="step">03</span><div><h2>{t('planEvidence')}</h2><p>{t('reviewSubtitle')}</p></div><span className="slot-mark">{slot}</span></div>
          <div className="result-tabs" role="group" aria-label={t('resultViews')}>
            <button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}>{t('worklist')}</button>
            <button className={tab === 'ties' ? 'active' : ''} disabled={!run || tiedCandidates.size === 0} onClick={() => setTab('ties')}>{t('ties')}</button>
            <button className={tab === 'detail' ? 'active' : ''} onClick={() => setTab('detail')}>{t('cellDetail')}</button>
            <button className={tab === 'compare' ? 'active' : ''} onClick={() => setTab('compare')}>{t('compare')}</button>
          </div>
          <div className="results-body">
            {error && <p className="message error" role="alert">{t(error)}</p>}
            {notice && <p className="message success" role="status">{t(notice)}</p>}
            {stale && <p className="message warning" role="status">{t('stale', { slot })}</p>}
            {tab === 'results' && (run ? <>
              <div className="metrics"><div><strong>{worklist.length}</strong><span>{t('worklistLimit', { k: draft.k })}</span></div>
                <div><strong>{num.format(run.rankedCount)}</strong><span>{t('evidenceComplete')}</span></div>
                <div><strong>{num.format(run.incompleteCount)}</strong><span>{t('evidenceIncomplete')}</span></div></div>
              <p className="micro">{t('runSummary', { recommended: run.recommendations.length, adjustments: scenario.adjustments.length })}{run.excludedCount > 0 && <> · {t('exclusions', { count: run.excludedCount })}</>}</p>
              {tiedCandidates.size > 0 && <button className="tie-summary" onClick={() => setTab('ties')}>
                <i className="swatch tied"/><span>{t('viewTies', { count: num.format(tiedCandidates.size) })}</span><Icon name="arrow" size={14}/>
              </button>}
              {run.recommendations.length < draft.k && <p className="message warning">{t('belowLimit', { k: draft.k })}</p>}
              <div className="recommendations">{worklist.map((item, index) => <article className="result-item" key={item.id}>
                <button className="cell-link" onClick={() => pickCell(item, true)}><span className="rank">{index + 1}</span><span>
                  <strong>{shortId(item)}</strong><small>{item.c ? speciesNames(item.focusIds) : t('manualReview')}</small></span></button>
                <div className="item-right"><strong>{item.score?.toFixed(1) ?? '—'}</strong>
                  <button className="text-button subtle" onClick={() => openAdjustment('remove', item)} aria-label={t('removeCell', { cell: shortId(item) })}>{t('remove')}</button></div>
              </article>)}</div>
              {slot === 'A' && <button className="secondary full" onClick={copyForComparison}><Icon name="compare"/>{slots.B ? t('editB') : t('copyToB')}</button>}
            </> : <div className="empty"><span className="empty-icon"><Icon name="map" size={24}/></span><h3>{t('emptyPlan')}</h3>
              <p>{t('emptyPlanHelp')}</p></div>)}
            {tab === 'ties' && (tiedCandidates.size > 0 ? <>
              <div className="tie-heading"><i className="swatch tied"/><h3>{t('tieCount', { count: num.format(tiedCandidates.size) })}</h3></div>
              <p className="micro">{t('tieHelp', { score: cutoff?.toFixed(1) ?? '—' })}</p>
              <div className="recommendations tie-list">{Array.from(tiedCandidates.values(), candidate =>
                <button key={candidate.id} className="difference" onClick={() => pickCell(candidate, true)}>
                  <span>{shortId(candidate)}</span><span>{candidate.longitude.toFixed(4)}°E, {candidate.latitude.toFixed(4)}°N</span>
                </button>)}</div>
            </> : <p className="micro">{t('noTies')}</p>)}
            {tab === 'detail' && (detail ? <>
              <div className="detail-title"><div><h3>{shortId(detail.cell)}</h3>
                <p>{detail.cell.longitude.toFixed(4)}°E, {detail.cell.latitude.toFixed(4)}°N</p></div>
                <span className={detail.score == null ? 'tag warm' : 'tag green'}>{detail.excluded ? t('excluded') : detail.score == null ? t('evidenceIncomplete') : t('points', { score: detail.score.toFixed(1) })}</span></div>
              <div className="explanation">{detail.excluded ? t('excludedCell')
                : detail.missingIds.length ? t('missingPredictions', { species: speciesNames(detail.missingIds) })
                : t('scoringExplanation', { count: detail.c, total: draft.speciesIds.length })}</div>
              {a && b && <div className="micro">{t('planScores', {
                a: a.scores[(detail.cell.row - catalog.grid.row0) * catalog.grid.width + detail.cell.col - catalog.grid.col0]?.toFixed(1) ?? '—',
                b: b.scores[(detail.cell.row - catalog.grid.row0) * catalog.grid.width + detail.cell.col - catalog.grid.col0]?.toFixed(1) ?? '—',
              })}</div>}
              <table className="detail-table"><thead><tr><th>{t('species')}</th><th>{t('prediction')}</th><th>{t('threshold')}</th><th>{t('status')}</th></tr></thead>
                <tbody>{catalog.species.map(s => <tr key={s.id}>
                  <td>{byName.get(s.id)}</td><td>{decimal(detail.values[s.id])}</td><td>{decimal(catalog.thresholds[s.id].value)}</td>
                  <td>{!draft.speciesIds.includes(s.id) ? t('notSelected') : detail.values[s.id] == null ? t('missing') : detail.focusIds.includes(s.id) ? <span className="tag green">{t('focus')}</span> : t('belowThreshold')}</td>
                </tr>)}</tbody></table>
              <p className="micro">{t('thresholdNote')}</p>
              {detailIsTie && <p className="message tie-message"><i className="swatch tied"/> {t('tieDetail')}</p>}
              <div className="detail-actions">
                {detailIsTie && replaceable.length > 0 && <button className="primary" disabled={busy}
                  onClick={() => openAdjustment('replace', { ...detail.cell, score: detail.score, c: detail.c, focusIds: detail.focusIds })}>
                  {t('replaceCell')}
                </button>}
                <button className="secondary" disabled={busy} onClick={() => changeDraft({ ...draft, excludedCellIds: detail.excluded
                  ? draft.excludedCellIds.filter(id => id !== detail.cell.id) : [...draft.excludedCellIds, detail.cell.id] })}>
                  {detail.excluded ? t('restoreCandidate') : t('excludeCandidate')}
                </button>
                {run && (detailInList ? <button className="secondary" onClick={() => openAdjustment('remove', worklist.find(item => item.id === detail.cell.id)!)}>{t('removeFromWorklist')}</button>
                  : <button className="secondary" disabled={worklist.length >= draft.k || detail.score == null || detail.excluded}
                    onClick={() => openAdjustment('add', { ...detail.cell, score: detail.score, c: detail.c, focusIds: detail.focusIds })}>{t('addToWorklist')}</button>)}
              </div>
            </> : <div className="empty"><span className="empty-icon"><Icon name="pin" size={24}/></span><h3>{t(detailError ?? (cell && draft.speciesIds.length ? 'loadingCell' : 'selectCell'))}</h3><p>{t('selectCellHelp')}</p></div>)}
            {tab === 'compare' && (a && b ? <>
              <div className="metrics compare-metrics"><div><strong>{comparison.common.length}</strong><span>{t('sharedRecommendations')}</span></div>
                <div><strong>{comparison.onlyA.length}</strong><span>{t('onlyA')}</span></div><div><strong>{comparison.onlyB.length}</strong><span>{t('onlyB')}</span></div></div>
              <p className="micro">{t('compareLimits', { a: a.input.k, b: b.input.k })}</p>
              <p className="micro">{t('compareSpecies', { a: a.input.speciesIds.length, b: b.input.speciesIds.length })}</p>
              {[...comparison.onlyA.map(item => ({ ...item, label: t('onlyA') })), ...comparison.onlyB.map(item => ({ ...item, label: t('onlyB') }))].map(item =>
                <button key={item.id} className="difference" onClick={() => pickCell(item, true)}><span>{shortId(item)}</span><span>{item.label} · {t('points', { score: item.score.toFixed(1) })}</span></button>)}
              {comparison.onlyA.length + comparison.onlyB.length === 0 && <p className="message success">{t('sameRecommendations')}</p>}
            </> : <div className="empty"><span className="empty-icon"><Icon name="compare" size={24}/></span><h3>{t('comparePlans')}</h3>
              <p>{t('compareHelp')}</p>
              {a && <button className="secondary" onClick={copyForComparison}>{slots.B ? t('editB') : t('copyB')}</button>}</div>)}
          </div>
          <div className="result-footer">
            <button className="secondary full" disabled={!run || !worklist.length || !!scenario.confirmedAt} onClick={confirm}>
              <Icon name="check"/>{scenario.confirmedAt && run ? t('planConfirmed') : t('confirmPlan')}</button>
            <div className="export-row">{(['csv', 'geojson', 'json'] as const).map(format => <button key={format}
              disabled={!run || !scenario.confirmedAt || !worklist.length} onClick={() => download(format)}><Icon name="download" size={13}/>{format === 'geojson' ? 'GeoJSON' : format.toUpperCase()}</button>)}</div>
          </div>
        </section>
      </div>
      <footer className="workspace-footer"><span>{t('footer')}</span><span>{catalog.policyVersion} · {catalog.dataVersion}</span></footer>
    </main>
    {adjust && <div className="scrim" onClick={() => setAdjust(null)}><form className="dialog" onClick={event => event.stopPropagation()} onSubmit={applyAdjustment} role="dialog" aria-modal="true" aria-labelledby="adjust-title">
      <h2 id="adjust-title">{adjust.action === 'replace' ? t('swapIn') : adjust.action === 'add' ? t('manualAdd') : t('removeFromWorklist')} {shortId(adjust.item)}</h2>
      <p>{adjust.action === 'replace'
        ? t('swapHelp', { count: worklist.length, k: draft.k })
        : t('adjustmentHelp')}</p>
      {adjust.action === 'replace' && <>
        <label htmlFor="replace-cell">{t('outgoingCell')}</label>
        <select id="replace-cell" required autoFocus value={replaceId} onChange={e => setReplaceId(e.target.value)}>
          <option value="">{t('chooseOutgoing')}</option>
          {replaceable.map(item => <option key={item.id} value={item.id}>{shortId(item)} · {t('points', { score: item.score?.toFixed(1) ?? '—' })}</option>)}
        </select>
      </>}
      <label htmlFor="adjust-reason">{adjust.action === 'replace' ? t('replacementReason') : t('adjustmentReason')}</label>
      <textarea id="adjust-reason" value={reason} maxLength={280} required autoFocus={adjust.action !== 'replace'} onChange={e => setReason(e.target.value)} />
      <div className="dialog-actions"><button className="secondary" type="button" onClick={() => setAdjust(null)}>{t('cancel')}</button>
        <button className="primary" disabled={!reason.trim() || (adjust.action === 'replace' && !replaceId)}>
          {adjust.action === 'replace' ? t('confirmReplacement') : t('recordAdjustment')}
        </button></div>
    </form></div>}
    {evidenceOpen && <div className="scrim" onClick={() => setEvidenceOpen(false)}><section className="dialog" onClick={event => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <h2 id="evidence-title">{t('dataAndRules')}</h2>
      <dl><dt>{t('spatialExtent')}</dt><dd>{t('extentNote')}</dd>
        <dt>{t('modelOutput')}</dt><dd>{t('modelNote')}</dd>
        <dt>{t('baselinePeriod')}</dt><dd>{catalog.baselinePeriod ?? t('periodPending')} · {t('nativeResolution', { resolution })}</dd>
        <dt>{t('selectionMethod')}</dt><dd>{t('methodNote')}</dd>
        <dt>{t('evidenceGaps')}</dt><dd>{t('gapsNote')}</dd>
        {catalog.releaseId && <><dt>{t('dataRelease')}</dt><dd>{catalog.releaseId}</dd></>}
        {catalog.source && <><dt>{t('dataSource')}</dt><dd>{catalog.source[language]}</dd></>}
        <dt>{t('dataVersion')}</dt><dd>{catalog.dataVersion} · {catalog.policyVersion}</dd></dl>
      <button className="primary full" onClick={() => setEvidenceOpen(false)}>{t('backToWorkspace')}</button>
    </section></div>}
  </>
}
