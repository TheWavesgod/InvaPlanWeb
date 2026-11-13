import { useEffect, useRef } from 'react'
import L from 'leaflet'
import type { Bounds, Catalog, Cell, Run } from './api'
import { cellBounds } from './api'
import type { Translate } from './i18n'

type Highlight = { cell: Cell; group: 'single' | 'tie' | 'common' | 'a' | 'b' }
const groupColors = { single: '#f3a93c', tie: '#216ac1', common: '#15ad8e', a: '#e89b34', b: '#8264da' }

function latLngBounds([west, south, east, north]: Bounds): L.LatLngBoundsExpression {
  return [[south, west], [north, east]]
}

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

export function MapView({ catalog, layer, run, highlights, selected, onPick, locateRequest, t }: {
  t: Translate
  catalog: Catalog
  layer: string
  run: Run | null
  highlights: Highlight[]
  selected: Cell | null
  onPick: (row: number, col: number) => void
  locateRequest: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const rasters = useRef<L.LayerGroup | null>(null)
  const outlines = useRef<L.LayerGroup | null>(null)
  const pick = useRef(onPick)
  pick.current = onPick

  useEffect(() => {
    const bounds = latLngBounds(catalog.grid.bounds)
    const instance = L.map(host.current!, {
      crs: L.CRS.EPSG4326, attributionControl: false, zoomControl: false, zoomSnap: 0.25, zoomDelta: 0.5,
      minZoom: 5, maxZoom: 15, scrollWheelZoom: false, preferCanvas: true,
    })
    map.current = instance
    rasters.current = L.layerGroup().addTo(instance)
    outlines.current = L.layerGroup().addTo(instance)
    instance.fitBounds(bounds, { padding: [28, 30] })
    L.control.scale({ imperial: false, position: 'bottomright' }).addTo(instance)
    instance.on('click', ({ latlng }: L.LeafletMouseEvent) => {
      const { row0, col0, width, height } = catalog.grid
      const [west, south, east, north] = catalog.grid.bounds
      const row = Math.floor((north - latlng.lat) / (north - south) * height)
      const col = Math.floor((latlng.lng - west) / (east - west) * width)
      if (row >= 0 && col >= 0 && row < height && col < width) pick.current(row + row0, col + col0)
    })
    const observer = new ResizeObserver(() => instance.invalidateSize())
    observer.observe(host.current!)
    return () => {
      observer.disconnect()
      instance.remove()
      map.current = null
    }
  }, [catalog])

  useEffect(() => {
    const instance = map.current
    if (!instance) return
    const control = L.control.zoom({ zoomInTitle: t('zoomIn'), zoomOutTitle: t('zoomOut') }).addTo(instance)
    return () => { control.remove() }
  }, [catalog, t])

  useEffect(() => {
    const group = rasters.current
    if (!group) return
    group.clearLayers()
    const bounds = latLngBounds(catalog.grid.bounds)
    if (layer !== 'priority' || run) {
      const src = layer === 'priority'
        ? scoreImage(catalog, run!.scores)
        : `/api/layers/${encodeURIComponent(layer)}.png`
      L.imageOverlay(src, bounds, { interactive: false, className: 'grid-raster' }).addTo(group)
    }
    L.rectangle(bounds, { color: '#425c53', weight: 1, dashArray: '4 4', fill: false, interactive: false }).addTo(group)
  }, [catalog, layer, run])

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
