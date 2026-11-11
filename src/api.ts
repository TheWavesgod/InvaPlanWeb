export type Bounds = [west: number, south: number, east: number, north: number]
export type Species = { id: string; name: string; latin: string; exploratory: boolean }
export type Grid = { bounds: Bounds; width: number; height: number; row0: number; col0: number }
export type Catalog = {
  name: string
  dataVersion: string
  policyVersion: string
  baselinePeriod: string | null
  species: Species[]
  grid: Grid
  thresholds: Record<string, { value: number; validCount: number }>
  maxK: number
}
export type Cell = { id: string; row: number; col: number; latitude: number; longitude: number }
export type Input = { speciesIds: string[]; k: number; excludedCellIds: string[] }
export type Recommendation = Cell & { score: number; c: number; focusIds: string[] }
export type Run = {
  input: Input
  dataVersion: string
  policyVersion: string
  rankedCount: number
  incompleteCount: number
  excludedCount: number
  positiveCount: number
  recommendations: Recommendation[]
  scores: (number | null)[]
}
export type Detail = {
  cell: Cell
  values: Record<string, number | null>
  score: number | null
  c: number
  focusIds: string[]
  missingIds: string[]
  excluded: boolean
}

export async function request<T>(path: string, input?: Input, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: input ? 'POST' : 'GET',
    body: input ? JSON.stringify(input) : undefined,
    headers: input ? { 'Content-Type': 'application/json' } : undefined,
    signal,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    const detail = error?.detail
    throw new Error(typeof detail === 'string' ? detail : `服务请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}

export function cellAtIndex(grid: Grid, index: number): Cell {
  const row = grid.row0 + Math.floor(index / grid.width)
  const col = grid.col0 + index % grid.width
  const [west, south, east, north] = cellBounds(grid, { row, col })
  return {
    id: `r${String(row).padStart(5, '0')}c${String(col).padStart(5, '0')}`,
    row, col, longitude: (west + east) / 2, latitude: (south + north) / 2,
  }
}

export function cellBounds(grid: Grid, cell: Pick<Cell, 'row' | 'col'>): Bounds {
  const [west, south, east, north] = grid.bounds
  const dx = (east - west) / grid.width
  const dy = (north - south) / grid.height
  const x = west + (cell.col - grid.col0) * dx
  const y = north - (cell.row - grid.row0) * dy
  return [x, y - dy, x + dx, y]
}
