import { useEffect, useMemo, useState } from 'react'
import { request } from './api'
import type { Cell, Run, ShortlistComparison } from './api'
import { requestFailure } from './i18n'
import type { Message, Translate } from './i18n'

export function useShortlistComparison(run: Run | null, worklist: Cell[]) {
  const input = useMemo(() => run && ({
    ...run.input,
    dataVersion: run.dataVersion, policyVersion: run.policyVersion,
    originalCellIds: run.recommendations.map(cell => cell.id),
    finalCellIds: worklist.map(cell => cell.id),
  }), [run, worklist])
  const [result, setResult] = useState<{
    input: typeof input; comparison?: ShortlistComparison; error?: Message
  } | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!input) return
    const abort = new AbortController()
    request<ShortlistComparison>('/api/shortlist/comparison', input, abort.signal)
      .then(comparison => { if (!abort.signal.aborted) setResult({ input, comparison }) })
      .catch(cause => { if (!abort.signal.aborted) setResult({ input, error: requestFailure(cause) }) })
    return () => abort.abort()
  }, [input, attempt])
  const current = result?.input === input ? result : null
  return {
    comparison: current?.comparison, error: current?.error,
    retry: () => { setResult(null); setAttempt(previous => previous + 1) },
  }
}

export function ShortlistReview({ review, speciesIds, names, t }: {
  review: ReturnType<typeof useShortlistComparison>; speciesIds: string[]; names: Map<string, string>; t: Translate
}) {
  if (review.error) return <p className="message error" role="alert">{t(review.error)}{' '}
    <button type="button" className="text-button" onClick={review.retry}>{t('retrySummary')}</button></p>
  if (!review.comparison) return <p className="micro" role="status">{t('loadingSummary')}</p>
  const { original, final } = review.comparison
  const columns = [original, final]
  return <section className="shortlist-review">
    <table className="detail-table">
      <caption>{t('shortlistReview')}</caption>
      <thead><tr><th scope="col">{t('shortlistMetric')}</th><th scope="col">{t('originalList')}</th><th scope="col">{t('currentList')}</th></tr></thead>
      <tbody>
        <tr><th scope="row">{t('siteCount')}</th><td>{original.cellCount}</td><td>{final.cellCount}</td></tr>
        <tr><th scope="row">{t('coveredSpecies')}</th>{columns.map((s, i) => <td key={i}>{s.coveredSpeciesIds.length} / {speciesIds.length}</td>)}</tr>
        <tr><th scope="row">{t('medianNearest')}</th>{columns.map((s, i) => <td key={i}>{s.medianNearestKm?.toFixed(2) ?? '—'}</td>)}</tr>
        {final.demoDerivedCellCount !== null && <tr><th scope="row">{t('demoDerivedCount')}</th><td>{original.demoDerivedCellCount}</td><td>{final.demoDerivedCellCount}</td></tr>}
      </tbody>
    </table>
    <details><summary>{t('speciesBreakdown')}</summary>
      <p className="micro">{t('coverageDefinition')}</p>
      <table className="detail-table"><thead><tr><th scope="col">{t('species')}</th><th scope="col">{t('originalList')}</th><th scope="col">{t('currentList')}</th></tr></thead>
        <tbody>{speciesIds.map(id => <tr key={id}><th scope="row">{names.get(id) ?? id}</th>
          <td>{original.speciesCellCounts[id]}</td><td>{final.speciesCellCounts[id]}</td>
        </tr>)}</tbody></table>
    </details>
  </section>
}
