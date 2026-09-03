import { useEffect, useState } from 'react'
import { IconRecovery } from '../components/Icons'
import {
  loadRecoveryData, money, percent, recoveryMetrics, type RecoveryAIResult,
} from '../lib/recovery'
import { useWorkspace } from '../store/workspace'

function Stat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="omx-rev-stat">
      <div className="omx-rev-stat-label">{label}</div>
      <div className="omx-rev-stat-value">{value}</div>
      <div className="omx-rev-stat-detail">{detail}</div>
    </div>
  )
}

export function DashboardView() {
  const setView = useWorkspace((s) => s.setView)
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
  const manualReview = data?.audit_trail.filter((r) => r.status === 'Manual review').length ?? 0
  const blocked = data?.audit_trail.filter((r) => r.status === 'Blocked').length ?? 0

  return (
    <div className="omx-scroll omx-rev-page">
      <div className="omx-rev-header">
        <div>
          <div className="omx-rev-kicker">Revora · Overview</div>
          <h1>Recovery operations dashboard</h1>
          <p>
            AI recommends. Deterministic controls authorize. Execution remains bounded.
          </p>
        </div>
        <button className="omx-btn primary" onClick={() => setView('recovery')}>
          Open Recovery Dashboard
        </button>
      </div>

      {error && (
        <div className="omx-rev-banner warn">Recovery API unavailable: {error}</div>
      )}

      {loading ? (
        <div className="omx-label" style={{ padding: 24 }}>Loading recovery metrics…</div>
      ) : m && (
        <>
          <div className="omx-rev-grid four">
            <Stat label="Revenue at risk" value={money(m.revenueAtRisk)} detail="Failed payments detected" />
            <Stat label="AI recovered" value={money(m.aiRevenue)} detail={`${m.aiRecovered} successful recoveries`} />
            <Stat label="Recovery rate" value={percent(m.recoveryRate)} detail="Of recoverable revenue" />
            <Stat label="Manual review" value={String(manualReview)} detail={`${blocked} blocked by policy`} />
          </div>

          <div className="omx-rev-grid three" style={{ marginTop: 14 }}>
            <Stat label="Total payments" value={m.totalPayments.toLocaleString()} detail={`${m.failedPayments} failed`} />
            <Stat label="AI candidates" value={String(m.aiCandidates)} detail={`${m.aiApproved} policy-approved`} />
            <Stat label="False approvals" value={String(m.falseApprovals)} detail="Safety benchmark metric" />
          </div>

          <section className="omx-rev-panel" style={{ marginTop: 22 }}>
            <h2>Recovery decision pipeline</h2>
            <p className="omx-rev-sub">
              Every failed payment moves through detect → diagnosis → evidence → policy → execution.
            </p>
            <div className="omx-rev-pipeline">
              {[
                ['Detect', 'Identify failed payments and recovery opportunities'],
                ['AI Diagnosis', 'Recommend a bounded recovery action'],
                ['Evidence', 'Verify the recommendation against transaction data'],
                ['Policy', 'Apply deterministic safety constraints'],
                ['Execute', 'Perform bounded simulated recovery'],
              ].map(([title, desc], i, arr) => (
                <div key={title} className="omx-rev-pipe-step">
                  <div className="omx-rev-pipe-num">{String(i + 1).padStart(2, '0')}</div>
                  <div>
                    <div className="omx-rev-pipe-title">{title}</div>
                    <div className="omx-rev-pipe-desc">{desc}</div>
                  </div>
                  {i < arr.length - 1 && <div className="omx-rev-pipe-arrow">↓</div>}
                </div>
              ))}
            </div>
          </section>

          <div className="omx-rev-quick" style={{ marginTop: 22 }}>
            <button className="omx-btn" onClick={() => setView('table')}>Recovery Queue</button>
            <button className="omx-btn" onClick={() => setView('audit')}>Audit Trail</button>
            <button className="omx-btn" onClick={() => setView('nova')}>Ask Revora</button>
            <button className="omx-btn" onClick={() => setView('compare')}>Recovery Analytics</button>
          </div>
        </>
      )}

      {!loading && !data && !error && (
        <div className="omx-empty">
          <div className="glyph"><IconRecovery size={34} /></div>
          <h3>No recovery data yet</h3>
          <p>Open the Recovery Dashboard once the backend benchmark is running.</p>
        </div>
      )}
    </div>
  )
}
