import { useEffect, useRef } from 'react'
import L from 'leaflet'
import type { AreaCity, AreaStrategy } from './areaTypes'
import type { Language, Translate } from './i18n'
import { languageTags } from './i18n'

export type CoverageMetric = 'gainPct' | 'persistentPct'
export const coverageMetricLabels = { gainPct: 'areaGainCoverage', persistentPct: 'areaPersistentCoverage' } as const
export const coveragePalette = ['#ffedd0', '#f6bd77', '#e77a53', '#ba4249', '#77223e']
export const noHabitatColor = '#bec2c8'
export const coverageColor = (value: number | null) =>
  value === null ? noHabitatColor : coveragePalette[Math.min(4, Math.floor(value / 20))]

export type CityFootprints = {
  type: 'FeatureCollection'
  features: {
    type: 'Feature'; properties: { cityId: string }
    geometry: { type: 'MultiPolygon'; coordinates: number[][][][] }
  }[]
}

type Props = {
  footprints: CityFootprints; cities: AreaCity[]; strategy: AreaStrategy; metric: CoverageMetric
  language: Language; t: Translate; onPick: (row: AreaCity) => void
}

export function CityCoverageMap({ footprints, cities, strategy, metric, language, t, onPick }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const bounds = useRef<L.LatLngBounds | null>(null)
  const features = useRef(new Map<string, L.Polygon>())
  const cityRows = useRef(new Map<string, AreaCity>())
  const pick = useRef(onPick)
  pick.current = onPick
  useEffect(() => {
    const element = host.current!
    const instance = L.map(element, { attributionControl: false, zoomControl: false, scrollWheelZoom: false,
      zoomAnimation: false, zoomSnap: 0.25, zoomDelta: 0.75, minZoom: 2, maxZoom: 12 })
    const layers = features.current
    const polygons = L.geoJSON(footprints, {
      onEachFeature(feature, layer) {
        const id = feature.properties.cityId
        const path = layer as L.Polygon
        layers.set(id, path)
        path.on('click', () => { const city = cityRows.current.get(id); if (city) pick.current(city) })
      },
    }).addTo(instance)
    const extent = polygons.getBounds()
    bounds.current = extent
    map.current = instance
    instance.fitBounds(extent, { padding: [16, 16] })
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(instance)
    const observer = new ResizeObserver(() => instance.invalidateSize({ pan: false }))
    observer.observe(element)
    return () => { observer.disconnect(); instance.remove(); layers.clear(); map.current = null }
  }, [footprints])

  useEffect(() => {
    const instance = map.current!
    const zoom = L.control.zoom({ zoomInTitle: t('zoomIn'), zoomOutTitle: t('zoomOut') }).addTo(instance)
    return () => { zoom.remove() }
  }, [t, footprints])

  useEffect(() => {
    const lookup = new Map(cities.map(city => [city.cityId, city]))
    cityRows.current = lookup
    const number = new Intl.NumberFormat(languageTags[language], { maximumFractionDigits: 1 })
    for (const [id, path] of features.current) {
      const row = lookup.get(id)
      path.unbindTooltip()
      const value = row?.summary[strategy][metric] ?? null
      path.setStyle({ color: row ? '#654d49' : '#adb6bb', weight: row ? 0.8 : 0.4,
        fillColor: row ? coverageColor(value) : '#e1e4e6', fillOpacity: row ? 1 : 0.38 })
      const el = path.getElement()
      if (el) {
        el.setAttribute('data-city-id', id)
        if (el instanceof SVGElement) el.style.pointerEvents = row ? 'auto' : 'none'
      }
      if (row) {
        const label = document.createElement('span')
        const name = language === 'zh' ? row.nameZh : row.nameEn
        const coverage = value === null ? t('areaNoEligible') : number.format(value) + '%'
        label.textContent = name + ' · ' + coverage
        path.bindTooltip(label, { sticky: true })
      }
    }
  }, [cities, metric, strategy, language, t, footprints])

  // Changing a search frames its matches. Switching language or metric keeps the current view.
  const matchingIds = cities.map(city => city.cityId).join(',')
  useEffect(() => {
    const extent = L.latLngBounds([])
    for (const id of matchingIds.split(',')) {
      const feature = features.current.get(id)
      if (feature) extent.extend(feature.getBounds())
    }
    if (extent.isValid()) map.current?.fitBounds(extent, { padding: [16, 16], maxZoom: 9 })
  }, [matchingIds, footprints])

  useEffect(() => {
    const instance = map.current!
    const labels = L.layerGroup().addTo(instance)
    const candidates = [...cities].sort((a, b) => b.areaKm2 - a.areaKm2)
    function labelCities() {
      labels.clearLayers()
      const placed: L.Bounds[] = []
      const size = instance.getSize()
      for (const city of candidates) {
        const polygon = features.current.get(city.cityId)
        if (!polygon) continue
        const anchor = polygon.getBounds().getCenter()
        const p = instance.latLngToContainerPoint(anchor)
        const name = language === 'zh' ? city.nameZh : city.nameEn
        const end = p.add([18 + name.length * (language === 'zh' ? 13 : 8), 12])
        const box = L.bounds(p.subtract([3, 12]), end)
        if (p.x < 5 || p.y < 35 || end.x > size.x - 5 || end.y > size.y - 20
            || placed.some(other => other.intersects(box))) continue
        const label = document.createElement('span')
        label.textContent = name
        L.tooltip({permanent: true, direction: 'right', offset: [4, 0], className: 'city-locator'})
          .setLatLng(anchor).setContent(label).addTo(labels)
        placed.push(box)
      }
    }
    labelCities()
    instance.on('moveend resize', labelCities)
    return () => { instance.off('moveend resize', labelCities); labels.remove() }
  }, [cities, language, footprints])

  return <div className="city-map-frame">
    <div className="city-map-canvas" ref={host} role="region" aria-label={t('areaCoverageMap')} />
    <button type="button" className="city-map-reset quiet" onClick={() => {
      if (bounds.current) map.current?.fitBounds(bounds.current, { padding: [16, 16] })
    }}>{t('areaFitCities')}</button>
  </div>
}
