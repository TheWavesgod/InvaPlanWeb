import { useEffect, useRef } from 'react'
import L from 'leaflet'
import type { Catalog, Cell, Run } from './api'
import { cellAtIndex, cellBounds } from './api'
import { latLngBounds, useGridMap } from './useGridMap'
import type { Translate } from './i18n'

type Highlight = { cell: Cell; group: 'single' | 'tie' | 'common' | 'a' | 'b' }
const groupColors = { single: '#f3a93c', tie: '#216ac1', common: '#15ad8e', a: '#e89b34', b: '#8264da' }

function scoreImage(catalog: Catalog, scores: Run['scores']) {
  const canvas = document.createElement('canvas')
  canvas.width = catalog.grid.width
  canvas.height = catalog.grid.height
  const context = canvas.getContext('2d')!
  const image = context.createImageData(canvas.width, canvas.height)
  scores.forEach((score, index) => {
    const t = (score ?? 0) / 100
    image.data.set(score === null ? [130, 130, 130, 255] : [
      Math.round(255 - 111 * t), Math.round(239 - 215 * t), Math.round(218 - 168 * t), 255,
    ], index * 4)
  })
  context.putImageData(image, 0, 0)
  return canvas.toDataURL()
}

export function MapView({ catalog, layer, climateRaster, run, highlights, selected, onPick, locateRequest, t }: {
  t: Translate
  catalog: Catalog
  layer: string
  climateRaster: string | null
  run: Run | null
  highlights: Highlight[]
  selected: Cell | null
  onPick: (row: number, col: number) => void
  locateRequest: number
}) {
  const { host, map } = useGridMap(catalog.grid, t, onPick)
  const rasters = useRef<L.LayerGroup | null>(null)
  const outlines = useRef<L.LayerGroup | null>(null)
  useEffect(() => {
    const instance = map.current!
    rasters.current = L.layerGroup().addTo(instance)
    outlines.current = L.layerGroup().addTo(instance)
    return () => {
      rasters.current?.remove()
      outlines.current?.remove()
    }
  }, [catalog, map])

  useEffect(() => {
    const group = rasters.current
    if (!group) return
    group.clearLayers()
    const bounds = latLngBounds(catalog.grid.bounds)
    if (layer !== 'priority' || run) {
      const src = layer === 'priority'
        ? scoreImage(catalog, run!.scores)
        : climateRaster ?? `/api/layers/${encodeURIComponent(layer)}.png`
      L.imageOverlay(src, bounds, { interactive: false, className: 'grid-raster' }).addTo(group)
    }
    L.rectangle(bounds, { color: '#425c53', weight: 1, dashArray: '4 4', fill: false, interactive: false }).addTo(group)
    if (layer === 'priority' && catalog.binaryCoverage) {
      for (const index of catalog.binaryCoverage.demoOnlyIndices) {
        const cell = cellAtIndex(catalog.grid, index)
        L.rectangle(latLngBounds(cellBounds(catalog.grid, cell)), {
          color: '#542052', weight: 1.5, dashArray: '2 2', fill: false, interactive: false,
        }).addTo(group)
      }
    }
  }, [catalog, layer, climateRaster, run])

  useEffect(() => {
    const group = outlines.current
    if (!group) return
    group.clearLayers()
    for (const { cell, group: kind } of highlights) {
      const color = groupColors[kind]
      L.rectangle(latLngBounds(cellBounds(catalog.grid, cell)), {
        color, weight: kind === 'tie' ? 1 : 2,
        fillColor: kind === 'tie' ? '#8ec5ff' : color,
        fillOpacity: kind === 'tie' ? 0.85 : 0.8, interactive: false,
      }).addTo(group)
    }
    if (selected) {
      L.rectangle(latLngBounds(cellBounds(catalog.grid, selected)), {
        color: '#182d28', weight: 3, fill: false, interactive: false,
      }).addTo(group)
    }
  }, [catalog, highlights, selected])

  useEffect(() => {
    if (locateRequest > 0 && selected && map.current) {
      map.current.setView([selected.latitude, selected.longitude], Math.max(11, map.current.getZoom()))
    }
    // A map click selects a cell without moving the map. The locate button increments the request.
  }, [locateRequest]) // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="map-canvas" ref={host} aria-label={t('mapLabel')} />
}
