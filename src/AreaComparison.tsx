import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Catalog, Species } from './api'
import { cellAtIndex, request } from './api'
import { AreaMap } from './AreaMap'
import { exportArea } from './areaExport'
import { areaBudgets, areaHoldouts, areaStrategies } from './areaTypes'
import type {
  AreaBudget, AreaCellDetail, AreaComposite, AreaHabitats, AreaHabitatState,
  AreaHoldout, AreaResult, SpeciesArea,
} from './areaTypes'
import { languageTags, requestFailure } from './i18n'
import type { Language, Message, Translate } from './i18n'
import './area.css'

const cityUrl = '/api/area-comparisons/330400'
const strategyLabel = { level: 'areaLevel', change: 'areaChange', site: 'areaSite' } as const
const pathwayLabel = { ssp126: 'areaSsp126', ssp245: 'areaSsp245', ssp585: 'areaSsp585' } as const
const stateLabel = { persistent: 'areaPersistent', gain: 'areaGain' } as const
const habitatStates = ['persistent', 'gain'] as const
const habitatField = { persistent: 'persistentKm2', gain: 'gainKm2' } as const

// A changed URL must never expose a response (or an error) from the previous selection.
function useAreaRequest<T>(url: string | null) {
  const [attempt, setAttempt] = useState(0)
  const [response, setResponse] = useState<{ url: string; attempt: number; data?: T; error?: Message } | null>(null)
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    request<T>(url, undefined, controller.signal).then(data => {
      if (!controller.signal.aborted) setResponse({ url, attempt, data })
    }).catch(cause => {
      if (!controller.signal.aborted) setResponse({ url, attempt, error: requestFailure(cause) })
    })
    return () => controller.abort()
  }, [url, attempt])
  const current = response?.url === url && response?.attempt === attempt ? response : null
  return { data: current?.data, error: current?.error, retry: () => setAttempt(n => n + 1) }
}

function RequestStatus({ error, retry, children, t }: {
  error?: Message; retry: () => void; children: ReactNode; t: Translate
}) {
  return error
    ? <div className="area-status" role="alert">{t(error)} <button className="quiet" type="button" onClick={retry}>{t('areaRetry')}</button></div>
    : <p className="area-status" role="status">{children}</p>
}

const speciesName = (species: Species, language: Language) =>
  language === 'zh' ? species.name : (species.nameEn || species.latin)

function HabitatTable({ areas, name, t, format }: {
  areas: SpeciesArea; name: string; t: Translate; format: (value: number) => string
}) {
  return <section className="panel area-card">
    <h2>{t('areaSpeciesTitle', { name })}</h2>
    <dl className="area-totals">
      {habitatStates.map(state => <div key={state}>
        <dt>{t('areaCityTotal')} · {t(stateLabel[state])}</dt>
        <dd>{format(areas[habitatField[state]])} km²</dd>
      </div>)}
    </dl>
    <div className="area-table-wrap"><table className="area-table">
      <thead>
        <tr><th rowSpan={2} scope="col">{t('areaStrategy')}</th>
          {habitatStates.map(state => <th key={state} colSpan={2} scope="colgroup">{t(stateLabel[state])} (km²)</th>)}
        </tr>
        <tr>{habitatStates.map(state => [<th key={state + '-covered'} scope="col">{t('areaCovered')}</th>, <th key={state + '-missed'} scope="col">{t('areaMissedAmount')}</th>])}</tr>
      </thead>
      <tbody>{areaStrategies.map(strategy => <tr key={strategy}><th scope="row">{t(strategyLabel[strategy])}</th>
        {habitatStates.map(state => {
          const field = habitatField[state]
          const covered = areas[strategy][field]
          return [<td key={state + '-covered'}>{format(covered)}</td>,
            <td key={state + '-missed'}>{format(Math.max(0, areas[field] - covered))}</td>]
        })}
      </tr>)}</tbody>
    </table></div>
    <p className="area-note">{t('areaSpeciesHelp')}</p>
  </section>
}

function CellEvidence({ result, index, catalog, focus, language, t, format, setFocus }: {
  result: AreaResult; index: number | null; catalog: Catalog; focus: string; language: Language
  t: Translate; format: (value: number) => string; setFocus: (id: string) => void
}) {
  const position = index === null ? -1 : result.indices.indexOf(index)
  const cell = index !== null && position >= 0 ? cellAtIndex(result.grid, index) : null
  // The detail endpoint returns all drivers with any species chosen for numeric inspection.
  const evidenceId = focus || catalog.species[0]?.id
  const detailUrl = cell && evidenceId
    ? cityUrl + '/cells/' + cell.row + '/' + cell.col + '?holdout=' + result.holdout + '&species_id=' + encodeURIComponent(evidenceId)
    : null
  const detail = useAreaRequest<AreaCellDetail>(detailUrl)
  const data = detail.data
  const lookupName = (id: string) => {
    const item = catalog.species.find(s => s.id === id)
    return item ? speciesName(item, language) : id
  }
  const scoreFormat = new Intl.NumberFormat(languageTags[language], { maximumFractionDigits: 5 })
  const score = (value: number | null) => value == null ? '—' : scoreFormat.format(value)
  return <section className="panel area-card area-evidence" aria-live="polite">
    <h2>{t('areaCellTitle')}</h2>
    {!cell ? <p className="area-note">{t('areaCellPrompt')}</p> : <>
      <div className="area-cell-id">{cell.id}</div>
      <p className="area-note">{t('areaCellArea', { area: format(result.areas[position]) })}</p>
      {data ? <>
        <div className="area-table-wrap"><table className="area-table">
          <thead><tr><th scope="col">{t('areaStrategy')}</th><th scope="col">{t('areaScore')}</th><th scope="col">{t('areaCredited')}</th></tr></thead>
          <tbody>{areaStrategies.map(strategy => {
            const credited = result.allocations[strategy][position]
            const partial = credited > 0 && result.areas[position] - credited > 1e-8
            return <tr key={strategy}><th scope="row">{t(strategyLabel[strategy])}</th>
              <td>{score(data.scores[strategy])}</td><td>{format(credited)}{partial && <small className="area-partial">{t('areaPartial')}</small>}</td>
            </tr>
          })}</tbody>
        </table></div>
        <h3>{t('areaCellDrivers')}</h3>
        {data.drivers.length ? <ul className="area-drivers">
          {data.drivers.map(driver => <li key={driver.speciesId + driver.state}>
            <button type="button" className="text-button" onClick={() => setFocus(driver.speciesId)}>{lookupName(driver.speciesId)}</button>
            <span>{t(stateLabel[driver.state])}</span>
          </li>)}
        </ul> : <p className="area-note">{t('areaNoDrivers')}</p>}
        {focus ? <>
          <h3>{t('areaProjections', { name: lookupName(focus) })}</h3>
          <dl className="area-totals"><div><dt>{t('baseline')}</dt><dd>{score(data.baseline)}</dd></div>
            <div><dt>{t('threshold')}</dt><dd>{score(data.threshold)}</dd></div></dl>
          <div className="area-table-wrap"><table className="area-table area-projections">
            <thead><tr><th scope="col">{t('areaPathway')}</th><th scope="col">{t('areaPeriod')}</th>
              <th scope="col">{t('prediction')}</th><th scope="col">{t('areaRole')}</th></tr></thead>
            <tbody>{Object.entries(data.future).sort(([a], [b]) => a.localeCompare(b)).map(([id, value]) => {
              const scenario = catalog.climateScenarios?.find(s => s.id === id)
              const pathway = areaHoldouts.find(ssp => id.toLowerCase().startsWith(ssp))
              const period = scenario?.period ?? id.match(/\d{4}[-_]\d{4}/)?.[0].replace('_', '–')
              const withheld = pathway === result.holdout
              return <tr key={id} className={withheld ? 'area-heldout' : undefined}>
                <th scope="row">{pathway ? t(pathwayLabel[pathway]) : (scenario?.ssp ?? id)}</th><td>{period ?? '—'}</td>
                <td>{score(value)}</td><td>{t(withheld ? 'areaTesting' : 'areaTraining')}</td>
              </tr>
            })}</tbody>
          </table></div>
        </> : <p className="area-note">{t('areaCellSpeciesHelp')}</p>}
      </> : <RequestStatus error={detail.error} retry={detail.retry} t={t}>{t('loadingCell')}</RequestStatus>}
    </>}
  </section>
}

export function AreaComparison({ catalog, language, t }: { catalog: Catalog; language: Language; t: Translate }) {
  const [budget, setBudget] = useState<AreaBudget>(10)
  const [holdout, setHoldout] = useState<AreaHoldout>('ssp585')
  const [composite, setComposite] = useState<AreaComposite>('level')
  const [focus, setFocus] = useState('')
  const [habitatState, setHabitatState] = useState<AreaHabitatState>('gain')
  const [selected, setSelected] = useState<number | null>(null)
  const comparison = useAreaRequest<AreaResult>(cityUrl + '?budget=' + budget + '&holdout=' + holdout)
  const overlay = useAreaRequest<AreaHabitats>(focus
    ? cityUrl + '/species/' + encodeURIComponent(focus) + '?holdout=' + holdout : null)
  const result = comparison.data
  const cityArea = result?.areas.reduce((sum, value) => sum + value, 0)
  const focusSpecies = catalog.species.find(species => species.id === focus)
  const focusAreas = result?.species[focus]
  const habitat = focus ? overlay.data?.[habitatState] : null
  const areaFormat = new Intl.NumberFormat(languageTags[language], { maximumFractionDigits: 3 })
  const pctFormat = new Intl.NumberFormat(languageTags[language], { maximumFractionDigits: 1 })
  const percent = (value: number | null) => value == null ? '—' : pctFormat.format(value) + '%'
  const methods = [
    ['areaScope', 'areaScopeText'], ['areaWithheld', 'areaWithheldText'],
    ['areaCompositeMethod', 'areaCompositeText'], ['areaSiteMethod', 'areaSiteText'],
  ] as const

  return <main className="workspace area-workspace">
    <header className="page-head"><div><h1>{t('areaTitle')}</h1><p>{t('areaIntro')}</p></div></header>
    <div className="panel area-controls">
      <label>{t('areaBudget')}<select value={budget} onChange={event => setBudget(Number(event.target.value) as AreaBudget)}>
        {areaBudgets.map(value => <option key={value} value={value}>{value}%</option>)}
      </select></label>
      <label>{t('areaHoldout')}<select value={holdout} onChange={event => setHoldout(event.target.value as AreaHoldout)}>
        {areaHoldouts.map(value => <option key={value} value={value}>{t(pathwayLabel[value])}</option>)}
      </select></label>
      {result && cityArea !== undefined && <dl className="area-budget-totals area-totals">
        <div><dt>{t('areaBudgetAllocation')}</dt><dd>{areaFormat.format(cityArea * budget / 100)} km²</dd></div>
        <div><dt>{t('areaValidCity')}</dt><dd>{areaFormat.format(cityArea)} km²</dd></div>
        <div><dt>{t('areaValidCells')}</dt><dd>{areaFormat.format(result.indices.length)}</dd></div>
      </dl>}
    </div>

    {!result ? <RequestStatus t={t} error={comparison.error} retry={comparison.retry}>{t('areaLoading')}</RequestStatus>
    : !result.indices.length ? <p className="area-status" role="status">{t('areaEmpty')}</p> : <>
      <section className="panel area-card">
        <div className="area-card-head"><h2>{t('areaOverview')}</h2>
          <div className="area-exports" role="group" aria-label={t('areaExport')}>
            <button className="secondary" type="button" onClick={() => exportArea('csv', result, catalog)}>{t('areaExportCsv')}</button>
            <button className="secondary" type="button" onClick={() => exportArea('geojson', result, catalog)}>{t('areaExportGeojson')}</button>
          </div>
        </div>
        <div className="area-table-wrap"><table className="area-table area-summary">
          <thead><tr><th scope="col">{t('areaStrategy')}</th><th scope="col">{t('areaPersistentCoverage')}</th>
            <th scope="col">{t('areaGainCoverage')}</th><th scope="col">{t('areaGainSpecies')}</th></tr></thead>
          <tbody>{areaStrategies.map(strategy => <tr key={strategy}><th scope="row">{t(strategyLabel[strategy])}</th>
            <td>{percent(result.summary[strategy].persistentPct)}</td><td>{percent(result.summary[strategy].gainPct)}</td>
            <td>{result.summary[strategy].gainSpecies} / {result.eligibleGainSpecies}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="area-note">{t('areaOverviewNote')}</p>
      </section>

      <div className="area-layout">
        <div className="area-map-column">
          <section className="panel area-map-panel">
            <div className="area-card"><h2>{t('areaMapTitle')}</h2>
              <div className="area-map-controls">
                <label>{t('areaComposite')}<select value={composite} onChange={event => setComposite(event.target.value as AreaComposite)}>
                  {(['level', 'change'] as const).map(key => <option key={key} value={key}>{t(strategyLabel[key])}</option>)}
                </select></label>
                <label>{t('areaFocus')}<select value={focus} onChange={event => setFocus(event.target.value)}>
                  <option value="">{t('areaAllSpecies')}</option>
                  {catalog.species.map(item => {
                    const name = speciesName(item, language)
                    return <option key={item.id} value={item.id}>{name === item.latin ? name : name + ' · ' + item.latin} · {item.id}</option>
                  })}
                </select></label>
                <label>{t('areaState')}<select disabled={!focus} value={habitatState} onChange={event => setHabitatState(event.target.value as AreaHabitatState)}>
                  {habitatStates.map(state => <option key={state} value={state}>{t(stateLabel[state])}</option>)}
                </select></label>
              </div><p className="area-note">{t('areaFocusHelp')}</p>
            </div>
            {habitat?.length === 0 && <p className="area-overlay-note" role="status">{t('areaNoHabitat')}</p>}
            <div className="area-map-layers">
              <div className={habitat === undefined ? 'area-layer-loading' : undefined} aria-hidden={habitat === undefined} inert={habitat === undefined}>
                <AreaMap result={result} composite={composite} habitat={habitat ?? (focus ? [] : null)} selected={selected} onPick={setSelected} t={t}/>
              </div>
              {habitat === undefined && <RequestStatus t={t} error={overlay.error} retry={overlay.retry}>{t('areaLoadingOverlay')}</RequestStatus>}
            </div>
          </section>
          {focusSpecies && focusAreas && <HabitatTable areas={focusAreas} name={speciesName(focusSpecies, language)} t={t} format={areaFormat.format}/>}
        </div>
        <CellEvidence result={result} index={selected} catalog={catalog} focus={focus} language={language}
          t={t} format={areaFormat.format} setFocus={setFocus}/>
      </div>
    </>}
    <details className="panel area-method"><summary>{t('areaMethod')}</summary>
      <dl>{methods.map(([title, body]) => <div key={title}><dt>{t(title)}</dt><dd>{t(body)}</dd></div>)}</dl>
    </details>
  </main>
}
