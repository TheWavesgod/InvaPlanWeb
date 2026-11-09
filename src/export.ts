import { cellBounds } from './api'
import type { Catalog, Cell, Recommendation, Run } from './api'

export type Adjustment = { action: 'add' | 'remove'; cell: Cell; reason: string; timestamp: string }
export type WorkItem = Cell & { score: number | null; c: number; focusIds: string[] }
export type Decision = {
  run: Run
  worklist: WorkItem[]
  adjustments: Adjustment[]
  confirmedAt: string
}

function download(name: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function csvField(value: unknown) {
  const text = String(value ?? '')
  const safe = typeof value === 'string' && /^[\s]*[=+\-@]/.test(text) ? "'" + text : text
  return '"' + safe.replaceAll('"', '""') + '"'
}

export function exportDecision(format: 'json' | 'csv' | 'geojson', decision: Decision, catalog: Catalog) {
  const { scores, ...run } = decision.run
  const context = {
    region: catalog.name,
    bounds: catalog.grid.bounds,
    baselinePeriod: catalog.baselinePeriod,
    dataVersion: run.dataVersion,
    policyVersion: run.policyVersion,
    dataMode: 'real_sample_baseline',
    thresholds: Object.fromEntries(run.input.speciesIds.map(id => [id, catalog.thresholds[id]])),
    confirmedAt: decision.confirmedAt,
  }
  const original = new Set<Recommendation['id']>(run.recommendations.map(item => item.id))
  const attributes = (cell: WorkItem) => ({
    cellId: cell.id, longitude: cell.longitude, latitude: cell.latitude, score: cell.score,
    c: cell.c, n: run.input.speciesIds.length, focusSpecies: cell.focusIds.join(' / '),
    manual: !original.has(cell.id),
    remark: decision.adjustments.filter(change => change.cell.id === cell.id).at(-1)?.reason ?? '',
    dataVersion: context.dataVersion, policyVersion: context.policyVersion, dataMode: context.dataMode,
  })
  const name = 'invaplan-baseline-' + decision.confirmedAt.replace(/[:.]/g, '-')
  if (format === 'json') {
    download(name + '.json', JSON.stringify({ ...decision, run, context }, null, 2), 'application/json')
  } else if (format === 'csv') {
    const items = decision.worklist.map(attributes)
    const rows = [Object.keys(items[0]), ...items.map(item => Object.values(item))]
    download(name + '.csv', '\ufeff' + rows.map(row => row.map(csvField).join(',')).join('\r\n'), 'text/csv;charset=utf-8')
  } else {
    const features = decision.worklist.map(cell => {
      const [w, s, e, n] = cellBounds(catalog.grid, cell)
      return {
        type: 'Feature', properties: attributes(cell),
        geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
      }
    })
    download(name + '.geojson', JSON.stringify({ type: 'FeatureCollection', features, context }, null, 2), 'application/geo+json')
  }
}
