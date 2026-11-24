import { useEffect, useRef } from 'react'
import L from 'leaflet'
import type { Bounds, Grid } from './api'
import type { Translate } from './i18n'

export function latLngBounds([west, south, east, north]: Bounds): L.LatLngBoundsExpression {
  return [[south, west], [north, east]]
}

export function useGridMap(grid: Grid, t: Translate, onPick: (row: number, col: number) => void) {
  const host = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const pick = useRef(onPick)
  pick.current = onPick
  const { width, height, row0, col0 } = grid
  const [west, south, east, north] = grid.bounds

  useEffect(() => {
    const element = host.current!
    const instance = L.map(element, {
      crs: L.CRS.EPSG4326, attributionControl: false, zoomControl: false, zoomSnap: 0.25, zoomDelta: 0.5,
      minZoom: 5, maxZoom: 15, scrollWheelZoom: false, preferCanvas: true,
      // Switching budgets can unmount the map while Leaflet's zoom timer is pending.
      zoomAnimation: false,
    })
    map.current = instance
    instance.setView([(south + north) / 2, (west + east) / 2], 9)
    let fitted = false
    const resize = () => {
      if (!element.clientWidth || !element.clientHeight) return
      instance.invalidateSize({ pan: false })
      if (!fitted) {
        instance.fitBounds([[south, west], [north, east]], { padding: [24, 24] })
        fitted = true
      }
    }
    resize()
    L.control.scale({ imperial: false, position: 'bottomright' }).addTo(instance)
    instance.on('click', ({ latlng }: L.LeafletMouseEvent) => {
      const row = Math.floor((north - latlng.lat) / (north - south) * height)
      const col = Math.floor((latlng.lng - west) / (east - west) * width)
      if (row >= 0 && row < height && col >= 0 && col < width) pick.current(row + row0, col + col0)
    })
    const observer = new ResizeObserver(resize)
    observer.observe(element)
    return () => { observer.disconnect(); instance.remove(); map.current = null }
  }, [west, south, east, north, row0, col0, width, height])

  useEffect(() => {
    const control = L.control.zoom({ zoomInTitle: t('zoomIn'), zoomOutTitle: t('zoomOut') }).addTo(map.current!)
    return () => { control.remove() }
  }, [t, west, south, east, north, row0, col0, width, height])
  return { host, map }
}
