import { useEffect, useState } from 'react'
import { loadRecoveryData, percent, recoveryMetrics, type RecoveryAIResult } from '../lib/recovery'

export function ModelQualityView() {
  const [data, setData] = useState<RecoveryAIResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadRecoveryData()
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const m = data ? recoveryMetrics(data) : null

  return (
    <div className="omx-scroll omx-rev-page">
      <div className="omx-rev-header">
        <div>
          <div className="omx-rev-kicker">Governance</div>
          <h1>Model Quality</h1>
          <p>
            AI-assisted recovery detection quality on the simulation benchmark —
            precision, recall, and false approval rate.
          </p>
        </div>
      </div>

      {error && <div className="omx-rev-banner warn">Recovery API unavailable: {error}</div>}
      {loading && <div className="omx-label" style={{ padding: 24 }}>Loading model metrics…</div>}

      {m && (
        <>
          <div className="omx-rev-grid three">
            <div className="omx-rev-stat accent">
              <div className="omx-rev-stat-label">Precision</div>
              <div className="omx-rev-stat-value">{percent(m.precision)}</div>
              <div className="omx-rev-stat-detail">Approved recoveries that succeeded</div>
            </div>
            <div className="omx-rev-stat accent">
              <div className="omx-rev-stat-label">Recall</div>
              <div className="omx-rev-stat-value">{percent(m.aiRecall)}</div>
              <div className="omx-rev-stat-detail">Ground-truth recoverable found</div>
            </div>
            <div className="omx-rev-stat accent">
              <div className="omx-rev-stat-label">F1 score</div>
              <div className="omx-rev-stat-value">{m.f1.toFixed(2)}</div>
              <div className="omx-rev-stat-detail">Harmonic mean of precision &amp; recall</div>
            </div>
          </div>

          <section className="omx-rev-panel" style={{ marginTop: 18 }}>
            <h2>Benchmark context</h2>
            <div className="omx-rev-grid four">
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">AI candidates</div>
                <div className="omx-rev-stat-value">{m.aiCandidates}</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">AI approved</div>
                <div className="omx-rev-stat-value">{m.aiApproved}</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">False approvals</div>
                <div className="omx-rev-stat-value">{m.falseApprovals}</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">Ground-truth recoverable</div>
                <div className="omx-rev-stat-value">{m.groundTruthRecoverable}</div>
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
