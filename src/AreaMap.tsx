import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import type { AreaResult } from './areaTypes'
import type { Translate } from './i18n'
import { cellAtIndex, cellBounds } from './api'
import { latLngBounds, useGridMap } from './useGridMap'

const colors = {
  base: '#d4d7da', level: '#c38522', site: '#316ea6', both: '#6e4587', missed: '#ac2f46',
}

export function AreaMap({ result, composite, habitat, selected, onPick, t }: {
  result: AreaResult
  composite: 'level' | 'change'
  habitat: number[] | null
  selected: number | null
  onPick: (index: number) => void
  t: Translate
}) {
  const { grid } = result
  const valid = useMemo(() => new Set(result.indices), [result.indices])
  const { host, map } = useGridMap(grid, t, (row, col) => {
    const index = (row - grid.row0) * grid.width + col - grid.col0
    if (valid.has(index)) onPick(index)
  })
  const focus = useMemo(() => habitat === null ? null : new Set(habitat), [habitat])
  const missed = useMemo(() => result.indices.filter((index, offset) => focus?.has(index)
    && result.allocations[composite][offset] === 0 && result.allocations.site[offset] === 0),
    [result, composite, focus])

  useEffect(() => {
    // EPSG:4326 makes the native row/column image linear in map coordinates.
    const canvas = document.createElement('canvas')
    canvas.width = grid.width
    canvas.height = grid.height
    const ctx = canvas.getContext('2d')!
    result.indices.forEach((index, offset) => {
      const a = result.allocations[composite][offset] > 0
      const b = result.allocations.site[offset] > 0
      let color = colors.base
      if (focus === null || focus.has(index)) {
        if (a && b) color = colors.both
        else if (a) color = colors.level
        else if (b) color = colors.site
        else if (focus) color = colors.missed
      }
      ctx.fillStyle = color
      ctx.fillRect(index % grid.width, Math.floor(index / grid.width), 1, 1)
    })
    const raster = L.imageOverlay(canvas.toDataURL(), latLngBounds(grid.bounds),
      { interactive: false, className: 'grid-raster' }).addTo(map.current!)
    return () => { raster.remove() }
  }, [result, grid, composite, focus, map])

  useEffect(() => {
    if (selected === null || !valid.has(selected)) return
    const bounds = cellBounds(grid, cellAtIndex(grid, selected))
    const outline = L.rectangle(latLngBounds(bounds), {
      color: '#1d1724', weight: 3, fill: false, interactive: false,
    }).addTo(map.current!)
    return () => { outline.remove() }
  }, [grid, selected, valid, map])

  function locateMissed() {
    const previous = selected === null ? -1 : missed.indexOf(selected)
    const index = missed[(previous + 1) % missed.length]
    const cell = cellAtIndex(grid, index)
    onPick(index)
    map.current?.setView([cell.latitude, cell.longitude], Math.max(map.current.getZoom(), 11))
  }

  return <div className="area-map-box">
    <div className="area-map-frame"><div ref={host} className="map-canvas" aria-label={t('areaMapLabel')} /></div>
    <div className="area-map-key">
      {([[colors.level, 'areaOnlyComposite'], [colors.site, 'areaOnlySite'], [colors.both, 'areaBoth'],
        ...(focus ? [[colors.missed, 'areaMissedByBoth'] as const] : []),
        [colors.base, focus ? 'areaOutsideHabitat' : 'areaNeither']] as const).map(([color, key]) =>
        <span key={key}><i style={{ background: color }} />{t(key)}</span>)}
    </div>
    <p className="area-map-credit">{t('areaMapCredit')}</p>
    {focus && <p className="area-map-action"><span>{t('areaMissedCount', { count: missed.length })}</span>
      {missed.length > 0 && <button className="quiet" type="button" onClick={locateMissed}>{t('areaLocateMissed')}</button>}</p>}
  </div>
}
