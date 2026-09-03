/** Display-only recovery policy controls — backend behavior is fixed for this build. */

const POLICIES = [
  {
    name: 'Confidence gate',
    value: '≥ 0.72',
    detail: 'AI recommendations below threshold route to manual review.',
  },
  {
    name: 'Retry limit',
    value: '3 attempts',
    detail: 'Transactions at retry limit cannot receive automated retries.',
  },
  {
    name: 'Automatic amount limit',
    value: '₹25,000',
    detail: 'Higher-value recoveries require manual authorization.',
  },
  {
    name: 'Allowed actions',
    value: 'retry_payment, change_method',
    detail: 'Unsupported actions are blocked regardless of AI confidence.',
  },
  {
    name: 'Evidence verification',
    value: 'Required',
    detail: 'Diagnosis must match failure type, code, and retry history.',
  },
  {
    name: 'Idempotency',
    value: 'Batch-level',
    detail: 'Duplicate recovery attempts within a benchmark run are blocked.',
  },
  {
    name: 'Execution mode',
    value: 'Bounded simulation',
    detail: 'No real-money execution — outcomes are measured against ground truth.',
  },
  {
    name: 'Audit logging',
    value: 'Always on',
    detail: 'Every recommendation, policy decision, and outcome is recorded.',
  },
]

const STOPPING_RULES = [
  {
    title: 'STOP — low confidence',
    detail: 'Diagnosis below confidence threshold → manual review escalation.',
  },
  {
    title: 'STOP — unsupported evidence',
    detail: 'Recommendation conflicts with failure evidence or retry history.',
  },
  {
    title: 'STOP — retry exhausted',
    detail: 'Retry limit reached → Revora routes to manual review.',
  },
  {
    title: 'STOP — amount exceeds limit',
    detail: 'Transaction amount above automatic authorization ceiling.',
  },
]

export function PoliciesView() {
  return (
    <div className="omx-scroll omx-rev-page">
      <div className="omx-rev-header">
        <div>
          <div className="omx-rev-kicker">Governance</div>
          <h1>Recovery Policies</h1>
          <p>
            Deterministic controls that authorize execution. AI recommendations require
            policy authorization before any automated recovery action.
          </p>
        </div>
        <div className="omx-rev-banner info">
          Display-only — policy thresholds are fixed in this benchmark build.
        </div>
      </div>

      <section className="omx-rev-panel">
        <h2>Active policy controls</h2>
        <div className="omx-rev-policy-grid">
          {POLICIES.map((p) => (
            <div key={p.name} className="omx-rev-policy-card">
              <div className="omx-rev-policy-name">{p.name}</div>
              <div className="omx-rev-policy-value">{p.value}</div>
              <div className="omx-rev-policy-detail">{p.detail}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="omx-rev-panel" style={{ marginTop: 14 }}>
        <h2>Stopping rules</h2>
        <p className="omx-rev-sub">
          Hard stops that prevent unsafe or unsupported automated execution.
        </p>
        <div className="omx-rev-stop-grid">
          {STOPPING_RULES.map((r) => (
            <div key={r.title} className="omx-rev-stop-card">
              <div className="omx-rev-stop-title">{r.title}</div>
              <div className="omx-rev-stop-detail">{r.detail}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
