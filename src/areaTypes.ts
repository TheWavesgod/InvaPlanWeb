import type { Grid } from './api'

export const areaStrategies = ['level', 'change', 'site'] as const
export const areaBudgets = [5, 10, 20] as const
export const areaHoldouts = ['ssp126', 'ssp245', 'ssp585'] as const
export type AreaStrategy = typeof areaStrategies[number]
export type AreaComposite = Exclude<AreaStrategy, 'site'>
export type AreaHoldout = typeof areaHoldouts[number]
export type AreaBudget = typeof areaBudgets[number]
export type AreaHabitatState = 'persistent' | 'gain'

export type HabitatArea = { persistentKm2: number; gainKm2: number }
export type SpeciesArea = HabitatArea & Record<AreaStrategy, HabitatArea>
export type AreaSummary = { persistentPct: number | null; gainPct: number | null; gainSpecies: number | null }
export type AreaCity = {
  cityId: string
  nameZh: string
  nameEn: string
  cellCount: number
  areaKm2: number
  budgetKm2: number
  summary: Record<AreaStrategy, AreaSummary>
  eligibleGainSpecies: number | null
}
export type AreaDirectory = {
  budget: AreaBudget
  holdout: AreaHoldout
  cities: AreaCity[]
}
export type AreaResult = {
  version: string
  cityId: string
  budget: AreaBudget
  holdout: AreaHoldout
  grid: Grid
  indices: number[]
  areas: number[]
  allocations: Record<AreaStrategy, number[]>
  summary: Record<AreaStrategy, AreaSummary>
  eligibleGainSpecies: number | null
  species: Record<string, SpeciesArea>
}

export type AreaHabitats = Record<AreaHabitatState, number[]>
export type AreaCellDetail = {
  drivers: { speciesId: string; state: AreaHabitatState }[]
  scores: Record<AreaStrategy, number | null>
  speciesId: string
  baseline: number | null
  threshold: number | null
  future: Record<string, number | null>
}
