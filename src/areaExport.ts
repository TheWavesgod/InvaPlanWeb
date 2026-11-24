import { cellAtIndex, cellBounds } from './api'
import type { Catalog } from './api'
import { areaStrategies } from './areaTypes'
import type { AreaResult } from './areaTypes'
import { csvField, download } from './export'

/** Both exports always describe the full, fixed 39-species portfolios. */
export function exportArea(format: 'csv' | 'geojson', result: AreaResult, catalog: Catalog) {
  const { cityId, holdout, budget, grid, version, allocations } = result
  const total = result.areas.reduce((sum, n) => sum + n, 0)
  const name = `invaplan-area-${cityId}-${holdout}-${budget}pct-${version}`
  const context = {
    cityId, budgetPct: budget, holdout, comparisonVersion: version,
    releaseId: catalog.releaseId, baselineVersion: catalog.dataVersion,
    policyVersion: catalog.policyVersion, cityAreaKm2: total, budgetKm2: total * budget / 100,
    targetSpeciesIds: catalog.species.map(s => s.id), evaluationPeriods: ['2041-2060', '2081-2100'],
    geometryUnit: 'full_native_cell', allocationUnit: 'km2', boundaryRule: 'fractional_last_cell',
    cutoffTableSha256: catalog.binaryCoverage?.thresholdTableSha256,
  }
  if (format === 'csv') {
    const rows: (string | number)[][] = [[
      'cityId', 'comparisonVersion', 'budgetPct', 'budgetKm2', 'holdout', 'speciesId', 'latin', 'strategy',
      'cityPersistentKm2', 'coveredPersistentKm2', 'missedPersistentKm2', 'cityGainKm2', 'coveredGainKm2', 'missedGainKm2',
    ]]
    for (const s of catalog.species) {
      const metrics = result.species[s.id]
      for (const strategy of areaStrategies) {
        const covered = metrics[strategy]
        rows.push([cityId, version, budget, context.budgetKm2, holdout, s.id, s.latin, strategy,
          metrics.persistentKm2, covered.persistentKm2, Math.max(0, metrics.persistentKm2 - covered.persistentKm2),
          metrics.gainKm2, covered.gainKm2, Math.max(0, metrics.gainKm2 - covered.gainKm2)])
      }
    }
    download(name + '.csv', '\ufeff' + rows.map(row => row.map(csvField).join(',')).join('\r\n'), 'text/csv;charset=utf-8')
    return
  }
  const features = result.indices.flatMap((index, i) => {
    if (!areaStrategies.some(k => allocations[k][i] > 0)) return []
    const cell = cellAtIndex(grid, index)
    const [w, s, e, n] = cellBounds(grid, cell)
    const area = result.areas[i]
    return [{
      type: 'Feature', id: cell.id,
      properties: {
        cellId: cell.id, row: cell.row, col: cell.col, fullCellKm2: area,
        levelKm2: allocations.level[i], changeKm2: allocations.change[i], siteKm2: allocations.site[i],
        partialStrategies: areaStrategies.filter(k => allocations[k][i] > 0 && allocations[k][i] < area),
      },
      geometry: { type: 'Polygon', coordinates: [[[w,s], [e,s], [e,n], [w,n], [w,s]]] },
    }]
  })
  download(name + '.geojson', JSON.stringify({ type: 'FeatureCollection', context, features }), 'application/geo+json')
}
