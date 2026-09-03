import { useEffect, useState } from 'react'
import { IconCompare } from '../components/Icons'
import {
  loadRecoveryData, money, percent, recoveryMetrics, type RecoveryAIResult,
} from '../lib/recovery'

function Bar({ label, baseline, ai, max }: {
  label: string; baseline: number; ai: number; max: number
}) {
  const bPct = max > 0 ? (baseline / max) * 100 : 0
  const aPct = max > 0 ? (ai / max) * 100 : 0
  return (
    <div className="omx-rev-bar-row">
      <div className="omx-rev-bar-label">{label}</div>
      <div className="omx-rev-bar-track">
        <div className="omx-rev-bar baseline" style={{ width: `${bPct}%` }} title={`Baseline: ${baseline}`} />
        <div className="omx-rev-bar ai" style={{ width: `${aPct}%` }} title={`AI-assisted: ${ai}`} />
      </div>
      <div className="omx-rev-bar-vals">
        <span>{baseline}</span>
        <span>{ai}</span>
      </div>
    </div>
  )
}

export function RecoveryAnalyticsView() {
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
  const maxOpps = m ? Math.max(m.baselineOpportunities, m.aiCandidates, 1) : 1
  const maxRev = m ? Math.max(m.baselineRevenue, m.aiRevenue, 1) : 1

  return (
    <div className="omx-scroll omx-rev-page">
      <div className="omx-rev-header">
        <div>
          <div className="omx-rev-kicker">Analytics</div>
          <h1>Recovery Analytics</h1>
          <p>Baseline detector vs AI-assisted recovery on the live simulation benchmark.</p>
        </div>
      </div>

      {error && <div className="omx-rev-banner warn">Recovery API unavailable: {error}</div>}
      {loading && <div className="omx-label" style={{ padding: 24 }}>Loading analytics…</div>}

      {!loading && m && (
        <>
          <div className="omx-rev-legend">
            <span><i className="baseline" /> Baseline</span>
            <span><i className="ai" /> AI-assisted</span>
          </div>

          <section className="omx-rev-panel">
            <h2>Opportunities &amp; recoveries</h2>
            <Bar label="Opportunities" baseline={m.baselineOpportunities} ai={m.aiCandidates} max={maxOpps} />
            <Bar label="Approved" baseline={m.baselineOpportunities} ai={m.aiApproved} max={maxOpps} />
            <Bar label="Successful recoveries" baseline={0} ai={m.aiRecovered} max={Math.max(m.aiRecovered, 1)} />
          </section>

          <section className="omx-rev-panel" style={{ marginTop: 14 }}>
            <h2>Recovered revenue</h2>
            <Bar label="Revenue (₹)" baseline={m.baselineRevenue} ai={m.aiRevenue} max={maxRev} />
            <div className="omx-rev-grid three" style={{ marginTop: 16 }}>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">Combined recovery</div>
                <div className="omx-rev-stat-value">{money(m.combinedRevenue)}</div>
                <div className="omx-rev-stat-detail">Baseline + AI incremental</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">Recovery rate</div>
                <div className="omx-rev-stat-value">{percent(m.recoveryRate)}</div>
                <div className="omx-rev-stat-detail">Of recoverable revenue</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">False approvals</div>
                <div className="omx-rev-stat-value">{m.falseApprovals}</div>
                <div className="omx-rev-stat-detail">Automated safety benchmark</div>
              </div>
            </div>
          </section>

          <section className="omx-rev-panel" style={{ marginTop: 14 }}>
            <h2>Model quality</h2>
            <div className="omx-rev-grid three">
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">Precision</div>
                <div className="omx-rev-stat-value">{percent(m.precision)}</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">Recall</div>
                <div className="omx-rev-stat-value">{percent(m.aiRecall)}</div>
              </div>
              <div className="omx-rev-stat">
                <div className="omx-rev-stat-label">F1</div>
                <div className="omx-rev-stat-value">{m.f1.toFixed(2)}</div>
              </div>
            </div>
          </section>
        </>
      )}

      {!loading && !data && !error && (
        <div className="omx-empty">
          <div className="glyph"><IconCompare size={34} /></div>
          <h3>No analytics data</h3>
          <p>Run the recovery benchmark to compare baseline vs AI-assisted performance.</p>
        </div>
      )}
    </div>
  )
}

/** @deprecated Use RecoveryAnalyticsView — kept for any legacy imports */
export const CompareView = RecoveryAnalyticsView
