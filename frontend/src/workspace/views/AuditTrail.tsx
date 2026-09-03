import { useEffect, useState } from 'react'
import {
  loadRecoveryData, money, percent,
  type RecoveryAuditRecord,
} from '../lib/recovery'

function StatusBadge({ status }: { status: RecoveryAuditRecord['status'] }) {
  const cls =
    status === 'Recovered' ? 'ok'
      : status === 'Manual review' ? 'warn'
        : 'muted'
  return <span className={`omx-rev-badge ${cls}`}>{status}</span>
}

export function AuditTrailView() {
  const [records, setRecords] = useState<RecoveryAuditRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    loadRecoveryData()
      .then((d) => { if (!cancelled) setRecords(d.audit_trail ?? []) })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="omx-scroll omx-rev-page">
      <div className="omx-rev-header">
        <div>
          <div className="omx-rev-kicker">Governance</div>
          <h1>Audit Trail</h1>
          <p>
            Every recovery candidate — what happened, why, which rules were checked,
            and what was recovered. AI recommends; policy authorizes.
          </p>
        </div>
      </div>

      {error && <div className="omx-rev-banner warn">Recovery API unavailable: {error}</div>}
      {loading && <div className="omx-label" style={{ padding: 24 }}>Loading audit records…</div>}

      {!loading && records.length === 0 && !error && (
        <div className="omx-empty">
          <h3>No audit records</h3>
          <p>Audit entries appear when the recovery benchmark has processed transactions.</p>
        </div>
      )}

      <div className="omx-rev-audit-list">
        {records.map((r) => {
          const open = expanded === r.transaction_id
          return (
            <article key={r.transaction_id} className="omx-rev-audit-card">
              <button
                type="button"
                className="omx-rev-audit-head"
                onClick={() => setExpanded(open ? null : r.transaction_id)}
                aria-expanded={open}
              >
                <div>
                  <div className="omx-rev-audit-id">{r.transaction_id}</div>
                  <div className="omx-rev-audit-meta">
                    {r.merchant_id} · {money(r.amount)} · {r.failure_code}
                  </div>
                </div>
                <StatusBadge status={r.status} />
              </button>

              {open && (
                <div className="omx-rev-audit-body">
                  <div className="omx-rev-audit-grid">
                    <div><span className="k">Failure type</span>{r.failure_type}</div>
                    <div><span className="k">Retry count</span>{r.retry_count}</div>
                    <div><span className="k">AI recommendation</span>{r.recommended_action}</div>
                    <div><span className="k">Evidence verdict</span>{r.evidence_verdict}</div>
                    <div><span className="k">Evidence confidence</span>{percent(r.evidence_confidence)}</div>
                    <div><span className="k">Policy action</span>{r.policy_action}</div>
                    <div><span className="k">Policy reason</span>{r.policy_reason}</div>
                    <div><span className="k">Execution</span>{r.attempted ? (r.success ? 'Succeeded' : 'Failed') : 'Not attempted'}</div>
                    <div><span className="k">Recovered</span>{money(r.amount_recovered)}</div>
                  </div>
                  {r.rules_checked.length > 0 && (
                    <div className="omx-rev-rules">
                      <span className="k">Rules checked</span>
                      {r.rules_checked.map((rule) => (
                        <span key={rule} className="omx-pill xs">{rule}</span>
                      ))}
                    </div>
                  )}
                  {r.execution_message && (
                    <p className="omx-rev-audit-msg">{r.execution_message}</p>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}
