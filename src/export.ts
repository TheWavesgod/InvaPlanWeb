import { cellBounds } from './api'
import type { Catalog, Cell, Recommendation, Run, ShortlistComparison } from './api'

type AdjustmentDetails = { cell: Cell; reason: string; timestamp: string }
export type Adjustment = AdjustmentDetails & (
  | { action: 'add' | 'remove' }
  | { action: 'replace'; replacedCell: Cell }
)
export type WorkItem = Cell & { score: number | null; c: number; focusIds: string[] }
export type Decision = {
  run: Run
  worklist: WorkItem[]
  adjustments: Adjustment[]
  confirmedAt: string
  shortlistComparison: ShortlistComparison
}

export function download(name: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function csvField(value: unknown) {
  const text = String(value ?? '')
  const safe = typeof value === 'string' && /^[\s]*[=+\-@]/.test(text) ? "'" + text : text
  return '"' + safe.replaceAll('"', '""') + '"'
}

export function exportDecision(format: 'json' | 'csv' | 'geojson', decision: Decision, catalog: Catalog) {
  const { shortlistComparison, ...saved } = decision
  const { original: before, final: after } = shortlistComparison
  const { scores, ...run } = decision.run
  const context = {
    region: catalog.name,
    releaseId: run.releaseId ?? catalog.releaseId,
    regionId: catalog.regionId,
    bounds: catalog.grid.bounds,
    baselinePeriod: catalog.baselinePeriod,
    dataVersion: run.dataVersion,
    policyVersion: run.policyVersion,
    tieBreak: run.input.tieBreak ?? 'cell_id',
    originalMedianNearestKm: before.medianNearestKm,
    shortlistComparison,
    dataMode: 'real_sample_baseline',
    thresholds: Object.fromEntries(run.input.speciesIds.map(id => [id, catalog.thresholds[id]])),
    ...(catalog.binaryCoverage && {
      thresholdTable: catalog.binaryCoverage.thresholdTable,
      thresholdTableSha256: catalog.binaryCoverage.thresholdTableSha256,
      archiveCellCount: catalog.binaryCoverage.archiveCellCount,
      demoOnlyCellCount: catalog.binaryCoverage.demoOnlyIndices.length,
    }),
    confirmedAt: decision.confirmedAt,
  }
  const original = new Set<Recommendation['id']>(run.recommendations.map(item => item.id))
  const attributes = (cell: WorkItem) => {
    const latest = decision.adjustments.filter(change => change.cell.id === cell.id).at(-1)
    return {
      cellId: cell.id, longitude: cell.longitude, latitude: cell.latitude, score: cell.score,
      ...(cell.binarySource && { binarySource: cell.binarySource }),
      c: cell.c, n: run.input.speciesIds.length, focusSpecies: cell.focusIds.join(' / '),
      manual: !original.has(cell.id) || latest?.action === 'replace',
      replacedCellId: latest?.action === 'replace' ? latest.replacedCell.id : '',
      remark: latest?.reason ?? '',
      dataVersion: context.dataVersion, policyVersion: context.policyVersion, dataMode: context.dataMode,
      tieBreak: context.tieBreak, originalMedianNearestKm: context.originalMedianNearestKm,
      releaseId: context.releaseId, regionId: context.regionId,
    }
  }
  const name = 'invaplan-baseline-' + decision.confirmedAt.replace(/[:.]/g, '-')
  if (format === 'json') {
    download(name + '.json', JSON.stringify({ ...saved, run, context }, null, 2), 'application/json')
  } else if (format === 'csv') {
    const items = decision.worklist.map((cell, index) => ({
      ...attributes(cell),
      originalCellCount: before.cellCount, finalCellCount: after.cellCount,
      originalCoveredSpeciesIds: before.coveredSpeciesIds.join(' / '), finalCoveredSpeciesIds: after.coveredSpeciesIds.join(' / '),
      originalSpeciesCellCounts: JSON.stringify(before.speciesCellCounts), finalSpeciesCellCounts: JSON.stringify(after.speciesCellCounts),
      finalMedianNearestKm: after.medianNearestKm,
      originalDemoDerivedCellCount: before.demoDerivedCellCount, finalDemoDerivedCellCount: after.demoDerivedCellCount,
      originalRecommendationIds: index === 0 ? [...original].join(' / ') : '',
      adjustmentHistory: index === 0 ? JSON.stringify(decision.adjustments) : '',
    }))
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
    const audit = { ...context, originalRecommendations: run.recommendations, adjustments: decision.adjustments }
    download(name + '.geojson', JSON.stringify({ type: 'FeatureCollection', features, context: audit }, null, 2), 'application/geo+json')
  }
}
