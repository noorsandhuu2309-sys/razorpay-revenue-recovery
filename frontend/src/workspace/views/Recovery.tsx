import { useEffect, useMemo, useRef, useState } from 'react'
type Status = 'Recovered' | 'Manual review' | 'Blocked'

interface RecoveryRow {
  id: string
  merchant: string
  amount: number
  method: string
  failure: string
  action: string
  confidence: number
  status: Status
}

interface RecoveryBenchmark {
  total_payments: number
  failed_payments: number
  ground_truth_recoverable: number
  baseline_opportunities: number
  ai_candidates: number
  ai_approved: number
  ai_successful_recoveries: number
  false_approvals: number
  revenue_at_risk: number
  baseline_revenue: number
  ai_revenue: number
  combined_revenue: number
  recovery_rate: number
  status: string
}
interface RecoveryAuditRecord {
  transaction_id: string
  merchant_id: string
  amount: number
  payment_method: string
  failure_code: string
  failure_type: string
  retry_count: number
  recommended_action: string
  evidence_verdict: string
  evidence_confidence: number
  allowed: boolean
  policy_action: string
  policy_reason: string
  rules_checked: string[]
  attempted: boolean
  success: boolean
  amount_recovered: number
  execution_message: string
  status: Status
}
interface RecoveryAIResult {
  ai_candidates: number
  ai_approved: number
  ai_successful_recoveries: number
  false_approvals: number
  ai_revenue: number
  status: string
    audit_trail: RecoveryAuditRecord[]
      total_payments: number
  failed_payments: number
  ground_truth_recoverable: number
  baseline_opportunities: number
  baseline_revenue: number
  revenue_at_risk: number
  combined_revenue: number
  recovery_rate: number
}



function money(value: number): string {
  return `₹${value.toLocaleString('en-IN', {
    maximumFractionDigits: 0,
  })}`
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function MetricCard({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string
  value: string
  detail: string
  accent?: boolean
}) {
  return (
    <div
      style={{
        background:
          'linear-gradient(145deg, rgba(255,255,255,.035), rgba(255,255,255,.012))',
        border: '1px solid rgba(255,255,255,.08)',
        borderRadius: 14,
        padding: '18px 20px',
        minHeight: 118,
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          fontSize: 11,
          letterSpacing: '.14em',
          textTransform: 'uppercase',
          color: 'rgba(255,255,255,.45)',
          marginBottom: 12,
        }}
      >
        {label}
      </div>

      <div
        style={{
          fontSize: 27,
          lineHeight: 1,
          fontWeight: 600,
          color: accent ? '#d9b45a' : '#eee9dc',
          letterSpacing: '-.03em',
        }}
      >
        {value}
      </div>

      <div
        style={{
          marginTop: 10,
          fontSize: 12,
          color: 'rgba(255,255,255,.42)',
        }}
      >
        {detail}
      </div>
    </div>
  )
}

function PipelineStep({
  number,
  title,
  description,
  state,
}: {
  number: string
  title: string
  description: string
  state: 'done' | 'guard'
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 14,
        padding: '14px 0',
        borderBottom: '1px solid rgba(255,255,255,.055)',
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          flexShrink: 0,
          borderRadius: 8,
          display: 'grid',
          placeItems: 'center',
          border: `1px solid ${
            state === 'guard'
              ? 'rgba(217,180,90,.35)'
              : 'rgba(255,255,255,.12)'
          }`,
          color: state === 'guard' ? '#d9b45a' : '#bdb7a8',
          fontSize: 11,
          fontFamily: 'monospace',
        }}
      >
        {number}
      </div>

      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: '#e8e3d7',
          }}
        >
          {title}
        </div>

        <div
          style={{
            marginTop: 4,
            fontSize: 11,
            lineHeight: 1.5,
            color: 'rgba(255,255,255,.42)',
          }}
        >
          {description}
        </div>
      </div>
    </div>
  )
}

function StatusPill({ status }: { status: Status }) {
  const recovered = status === 'Recovered'
  const blocked = status === 'Manual review'

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 9px',
        borderRadius: 999,
        fontSize: 10,
        letterSpacing: '.04em',
        border: `1px solid ${
          recovered
            ? 'rgba(117,190,137,.25)'
            : blocked
              ? 'rgba(217,180,90,.25)'
              : 'rgba(255,255,255,.1)'
        }`,
        color: recovered
          ? '#9bc9a5'
          : blocked
            ? '#d9b45a'
            : '#aaa49a',
        background: recovered
          ? 'rgba(117,190,137,.06)'
          : blocked
            ? 'rgba(217,180,90,.06)'
            : 'rgba(255,255,255,.025)',
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: recovered
            ? '#9bc9a5'
            : blocked
              ? '#d9b45a'
              : '#777',
        }}
      />
      {status}
    </span>
  )
}

export function RecoveryView() {
  const [filter, setFilter] = useState<'all' | 'recovered' | 'review'>('all')

  const [benchmark, setBenchmark] = useState<RecoveryBenchmark | null>(null)
  const [auditTrail, setAuditTrail] = useState<RecoveryAuditRecord[]>([])
const [aiResult, setAiResult] = useState<RecoveryAIResult | null>(null)
const [loading, setLoading] = useState(true)
const [, setAiLoading] = useState(false)
const [error, setError] = useState<string | null>(null)
const aiRequestRef = useRef<Promise<RecoveryAIResult> | null>(null)


useEffect(() => {
  let cancelled = false


  async function loadAIRecovery() {
    try {
      setAiLoading(true)

      if (!aiRequestRef.current) {
        aiRequestRef.current = fetch('/api/recovery/ai').then(
          async (response) => {
            if (!response.ok) {
              throw new Error(
                `AI recovery API returned ${response.status}`,
              )
            }

            return (await response.json()) as RecoveryAIResult
          },
        )
      }

      const data = await aiRequestRef.current

if (!cancelled) {
  setAiResult(data)
  setAuditTrail(data.audit_trail ?? [])

  setBenchmark({
    total_payments: data.total_payments,
    failed_payments: data.failed_payments,
    ground_truth_recoverable: data.ground_truth_recoverable,
    baseline_opportunities: data.baseline_opportunities,
    revenue_at_risk: data.revenue_at_risk,
    baseline_revenue: data.baseline_revenue,
    ai_revenue: data.ai_revenue,
    combined_revenue: data.combined_revenue,
    recovery_rate: data.recovery_rate,
    ai_candidates: data.ai_candidates,
    ai_approved: data.ai_approved,
    ai_successful_recoveries: data.ai_successful_recoveries,
    false_approvals: data.false_approvals,
    status: data.status,
  })
}
    } catch (err) {
      if (!cancelled) {
        console.error(
          'AI recovery benchmark unavailable:',
          err,
        )
      }
    } finally {
      if (!cancelled) {
        setAiLoading(false)
      }
    }
  }

  
  loadAIRecovery()

  return () => {
    cancelled = true
  }
}, [])

const rows = useMemo<RecoveryRow[]>(() => {
  const liveRows: RecoveryRow[] = auditTrail.map((record) => ({
    id: record.transaction_id,
    merchant: record.merchant_id,
    amount: record.amount,
    method: record.payment_method,
    failure: record.failure_code,
    action: record.policy_action,
    confidence: record.evidence_confidence,
    status: record.status,
  }))

  if (filter === 'recovered') {
    return liveRows.filter((row) => row.status === 'Recovered')
  }

  if (filter === 'review') {
    return liveRows.filter((row) => row.status === 'Manual review')
  }

  return liveRows
}, [auditTrail, filter])

const precision =
  aiResult && aiResult.ai_approved > 0
    ? (aiResult.ai_approved - aiResult.false_approvals) /
      aiResult.ai_approved
    : 0

const aiRecall =
  benchmark && benchmark.ground_truth_recoverable > 0
    ? (aiResult?.ai_successful_recoveries ?? 0) /
      benchmark.ground_truth_recoverable
    : 0

  const f1 =
    precision + aiRecall > 0
      ? (2 * precision * aiRecall) / (precision + aiRecall)
      : 0

const display = {
  totalPayments: benchmark?.total_payments ?? 0,
  failedPayments: benchmark?.failed_payments ?? 0,
  groundTruthRecoverable: benchmark?.ground_truth_recoverable ?? 0,
  baselineOpportunities: benchmark?.baseline_opportunities ?? 0,

  aiCandidates: aiResult?.ai_candidates ?? 0,
  aiApproved: aiResult?.ai_approved ?? 0,
  aiSuccessfulRecoveries:
    aiResult?.ai_successful_recoveries ?? 0,
  aiFalseApprovals: aiResult?.false_approvals ?? 0,

  baselineRevenue: benchmark?.baseline_revenue ?? 0,
  aiRevenue: aiResult?.ai_revenue ?? 0,

  combinedRevenue:
    (benchmark?.baseline_revenue ?? 0) +
    (aiResult?.ai_revenue ?? 0),

  recoveryRate:
    benchmark?.revenue_at_risk
      ? (
          ((benchmark.baseline_revenue ?? 0) +
            (aiResult?.ai_revenue ?? 0)) /
          benchmark.revenue_at_risk
        )
      : 0,
}

  return (
    <div
      style={{
        height: '100%',
        overflow: 'auto',
        boxSizing: 'border-box',
        padding: '28px 32px 48px',
        color: '#e8e3d7',
        background:
          'radial-gradient(circle at 80% 0%, rgba(217,180,90,.045), transparent 34%), #090909',
      }}
    >
      <div
        style={{
          maxWidth: 1450,
          margin: '0 auto',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 24,
            marginBottom: 28,
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                marginBottom: 9,
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: '#d9b45a',
                  boxShadow: '0 0 12px rgba(217,180,90,.45)',
                }}
              />

              <span
                style={{
                  fontSize: 10,
                  letterSpacing: '.2em',
                  textTransform: 'uppercase',
                  color: '#d9b45a',
                }}
              >
                Revenue Recovery
              </span>
            </div>

            <h1
              style={{
                margin: 0,
                fontSize: 30,
                lineHeight: 1.1,
                fontWeight: 600,
                letterSpacing: '-.035em',
                color: '#eee9dc',
              }}
            >
              Recover revenue.
              <br />
              <span style={{ color: 'rgba(238,233,220,.48)' }}>
                Defend every decision.
              </span>
            </h1>

            <p
              style={{
                margin: '12px 0 0',
                maxWidth: 650,
                fontSize: 13,
                lineHeight: 1.65,
                color: 'rgba(255,255,255,.45)',
              }}
            >
              REVORA identifies failed payments, asks a reasoning model to
              diagnose the failure, verifies the diagnosis against transaction
              evidence, and applies deterministic safety policies before any
              automated recovery is authorized.
            </p>
          </div>

          <div
            style={{
              flexShrink: 0,
              padding: '9px 12px',
              borderRadius: 9,
              border: '1px solid rgba(117,190,137,.2)',
              background: 'rgba(117,190,137,.035)',
              color: '#9bc9a5',
              fontSize: 10,
              letterSpacing: '.08em',
              textTransform: 'uppercase',
            }}
          >
            {loading ? '◌ Loading benchmark' : '● Live simulation benchmark'}
          </div>
        </div>

        {error && (
          <div
            style={{
              marginBottom: 18,
              padding: '12px 14px',
              borderRadius: 9,
              border: '1px solid rgba(217,180,90,.2)',
              background: 'rgba(217,180,90,.035)',
              color: '#d9b45a',
              fontSize: 11,
            }}
          >
            Recovery API unavailable: {error}
          </div>
        )}

        {/* Main revenue cards */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            gap: 12,
            marginBottom: 12,
          }}
        >
          <MetricCard
            label="Revenue at risk"
            value={money(display.baselineRevenue)}
            detail="Detected baseline opportunity"
            accent
          />

          <MetricCard
            label="AI incremental"
            value={money(display.aiRevenue)}
            detail={`${display.aiApproved} additional approved recoveries`}
            accent
          />

          <MetricCard
            label="Combined recovered"
            value={money(display.combinedRevenue)}
            detail="Baseline + AI recovery"
            accent
          />

          <MetricCard
            label="Recovery rate"
            value={percent(display.recoveryRate)}
            detail="Of recoverable revenue"
          />
        </div>

        {/* Operational metrics */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            gap: 12,
            marginBottom: 26,
          }}
        >
          <MetricCard
            label="Payments"
            value={display.totalPayments.toLocaleString()}
            detail={`${display.failedPayments} failed`}
          />

          <MetricCard
            label="Baseline opportunities"
            value={display.baselineOpportunities.toString()}
            detail="High-confidence detector output"
          />

          <MetricCard
            label="AI candidates"
            value={display.aiCandidates.toString()}
            detail={`${display.aiApproved} approved`}
          />

          <MetricCard
            label="False approvals"
            value={display.aiFalseApprovals.toString()}
            detail="Automated safety benchmark"
          />
        </div>

        {/* Pipeline + quality */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1.4fr) minmax(320px, .8fr)',
            gap: 14,
            marginBottom: 26,
          }}
        >
          <section
            style={{
              border: '1px solid rgba(255,255,255,.075)',
              borderRadius: 14,
              background: 'rgba(255,255,255,.018)',
              padding: '20px 22px',
            }}
          >
            <div
              style={{
                fontSize: 11,
                letterSpacing: '.15em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,.42)',
                marginBottom: 4,
              }}
            >
              Recovery decision pipeline
            </div>

            <div
              style={{
                fontSize: 13,
                color: '#ddd8cc',
                marginBottom: 8,
              }}
            >
              Every automated action passes through independent controls.
            </div>

            <PipelineStep
              number="01"
              title="Detect"
              description="Find failed payments that have characteristics suggesting a recoverable failure."
              state="done"
            />

            <PipelineStep
              number="02"
              title="AI diagnosis"
              description="Reasoning model diagnoses the failure and recommends retry, payment-method change, manual review, or no action."
              state="done"
            />

            <PipelineStep
              number="03"
              title="Evidence verification"
              description="Deterministic checks independently compare the diagnosis with failure type, failure code, and retry history."
              state="guard"
            />

            <PipelineStep
              number="04"
              title="Policy authorization"
              description="Hard limits enforce evidence verification, confidence, retry limits, allowed actions, and transaction amount."
              state="guard"
            />

            <PipelineStep
              number="05"
              title="Execute"
              description="Only policy-approved recovery actions can reach the execution layer."
              state="guard"
            />
          </section>

          <section
            style={{
              border: '1px solid rgba(255,255,255,.075)',
              borderRadius: 14,
              background: 'rgba(255,255,255,.018)',
              padding: '20px 22px',
            }}
          >
            <div
              style={{
                fontSize: 11,
                letterSpacing: '.15em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,.42)',
              }}
            >
              Model quality
            </div>

            <div
              style={{
                marginTop: 6,
                fontSize: 20,
                color: '#e8e3d7',
              }}
            >
              AI-assisted detection
            </div>

            <div
              style={{
                marginTop: 20,
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 10,
              }}
            >
              <div
                style={{
                  border: '1px solid rgba(255,255,255,.06)',
                  borderRadius: 10,
                  padding: 14,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: 'rgba(255,255,255,.4)',
                    textTransform: 'uppercase',
                    letterSpacing: '.1em',
                  }}
                >
                  Precision
                </div>

                <div
                  style={{
                    marginTop: 7,
                    fontSize: 24,
                    color: '#d9b45a',
                  }}
                >
                  {percent(precision)}
                </div>
              </div>

              <div
                style={{
                  border: '1px solid rgba(255,255,255,.06)',
                  borderRadius: 10,
                  padding: 14,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: 'rgba(255,255,255,.4)',
                    textTransform: 'uppercase',
                    letterSpacing: '.1em',
                  }}
                >
                  Recall
                </div>

                <div
                  style={{
                    marginTop: 7,
                    fontSize: 24,
                    color: '#d9b45a',
                  }}
                >
                  {percent(aiRecall)}
                </div>
              </div>

              <div
                style={{
                  border: '1px solid rgba(255,255,255,.06)',
                  borderRadius: 10,
                  padding: 14,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: 'rgba(255,255,255,.4)',
                    textTransform: 'uppercase',
                    letterSpacing: '.1em',
                  }}
                >
                  F1 score
                </div>

                <div
                  style={{
                    marginTop: 7,
                    fontSize: 24,
                    color: '#d9b45a',
                  }}
                >
                  {f1.toFixed(2)}
                </div>
              </div>

              <div
                style={{
                  border: '1px solid rgba(255,255,255,.06)',
                  borderRadius: 10,
                  padding: 14,
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: 'rgba(255,255,255,.4)',
                    textTransform: 'uppercase',
                    letterSpacing: '.1em',
                  }}
                >
                  False approvals
                </div>

                <div
                  style={{
                    marginTop: 7,
                    fontSize: 24,
                    color: '#9bc9a5',
                  }}
                >
                  {display.aiFalseApprovals}
                </div>
              </div>
            </div>

            <div
              style={{
                marginTop: 17,
                padding: 12,
                borderRadius: 9,
                background: 'rgba(217,180,90,.035)',
                border: '1px solid rgba(217,180,90,.11)',
                fontSize: 11,
                lineHeight: 1.55,
                color: 'rgba(255,255,255,.48)',
              }}
            >
              <span style={{ color: '#d9b45a' }}>Safety principle:</span>{' '}
              model confidence never authorizes a payment by itself. The
              deterministic policy layer has final authority.
            </div>
          </section>
        </div>

        {/* Transactions */}
        <section
          style={{
            border: '1px solid rgba(255,255,255,.075)',
            borderRadius: 14,
            background: 'rgba(255,255,255,.018)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '18px 20px',
              borderBottom: '1px solid rgba(255,255,255,.065)',
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 11,
                  letterSpacing: '.15em',
                  textTransform: 'uppercase',
                  color: 'rgba(255,255,255,.42)',
                }}
              >
                Recovery decisions
              </div>

              <div
                style={{
                  marginTop: 5,
                  fontSize: 13,
                  color: '#ddd8cc',
                }}
              >
                Recent simulated transactions
              </div>
            </div>

            <div
              style={{
                display: 'flex',
                gap: 5,
                padding: 3,
                borderRadius: 8,
                background: 'rgba(255,255,255,.035)',
              }}
            >
              {(
                [
                  ['all', 'All'],
                  ['recovered', 'Recovered'],
                  ['review', 'Review'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setFilter(key)}
                  style={{
                    border: 0,
                    borderRadius: 6,
                    padding: '6px 10px',
                    background:
                      filter === key
                        ? 'rgba(217,180,90,.12)'
                        : 'transparent',
                    color:
                      filter === key
                        ? '#d9b45a'
                        : 'rgba(255,255,255,.42)',
                    fontSize: 10,
                    cursor: 'pointer',
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: 12,
              }}
            >
              <thead>
                <tr>
                  {[
                    'Transaction',
                    'Merchant',
                    'Amount',
                    'Method',
                    'Failure',
                    'AI action',
                    'Confidence',
                    'Decision',
                  ].map((heading) => (
                    <th
                      key={heading}
                      style={{
                        textAlign: 'left',
                        padding: '11px 16px',
                        fontSize: 9,
                        fontWeight: 500,
                        letterSpacing: '.1em',
                        textTransform: 'uppercase',
                        color: 'rgba(255,255,255,.3)',
                        borderBottom: '1px solid rgba(255,255,255,.05)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td
                      style={{
                        padding: '13px 16px',
                        color: '#d9b45a',
                        fontFamily: 'monospace',
                        fontSize: 11,
                      }}
                    >
                      {row.id}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color: 'rgba(255,255,255,.58)',
                      }}
                    >
                      {row.merchant}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color: '#e5e0d5',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {money(row.amount)}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color: 'rgba(255,255,255,.5)',
                      }}
                    >
                      {row.method}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color: 'rgba(255,255,255,.55)',
                        fontFamily: 'monospace',
                        fontSize: 11,
                      }}
                    >
                      {row.failure}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color:
                          row.action === 'retry_payment'
                            ? '#d9b45a'
                            : 'rgba(255,255,255,.5)',
                        fontFamily: 'monospace',
                        fontSize: 10,
                      }}
                    >
                      {row.action}
                    </td>

                    <td
                      style={{
                        padding: '13px 16px',
                        color: '#bdb7a8',
                      }}
                    >
                      {percent(row.confidence)}
                    </td>

                    <td style={{ padding: '13px 16px' }}>
                      <StatusPill status={row.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        {/* Audit trail + safety controls */}
        <section
          style={{
            marginTop: 14,
            border: '1px solid rgba(255,255,255,.075)',
            borderRadius: 14,
            background: 'rgba(255,255,255,.018)',
            padding: '20px 22px',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: 20,
              marginBottom: 18,
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 11,
                  letterSpacing: '.15em',
                  textTransform: 'uppercase',
                  color: 'rgba(255,255,255,.42)',
                }}
              >
                Audit trail
              </div>

              <div
                style={{
                  marginTop: 6,
                  fontSize: 15,
                  color: '#ddd8cc',
                }}
              >
                Every recovery decision is explainable and policy-bounded.
              </div>

              <div
                style={{
                  marginTop: 6,
                  fontSize: 11,
                  lineHeight: 1.6,
                  color: 'rgba(255,255,255,.4)',
                  maxWidth: 720,
                }}
              >
                The model can recommend an action, but deterministic evidence
                checks and recovery policy decide whether execution is allowed.
              </div>
            </div>

            <div
              style={{
                padding: '7px 10px',
                borderRadius: 8,
                border: '1px solid rgba(117,190,137,.18)',
                background: 'rgba(117,190,137,.035)',
                color: '#9bc9a5',
                fontSize: 9,
                letterSpacing: '.1em',
                textTransform: 'uppercase',
                whiteSpace: 'nowrap',
              }}
            >
              Policy enforced
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
              gap: 8,
            }}
          >
            {[
              {
                step: '01',
                title: 'Detection',
                detail: 'Failure identified',
              },
              {
                step: '02',
                title: 'Diagnosis',
                detail: 'AI recommendation recorded',
              },
              {
                step: '03',
                title: 'Evidence',
                detail: 'Failure + history verified',
              },
              {
                step: '04',
                title: 'Authorization',
                detail: 'Safety policy evaluated',
              },
              {
                step: '05',
                title: 'Execution',
                detail: 'Approved action dispatched',
              },
            ].map((item) => (
              <div
                key={item.step}
                style={{
                  padding: '13px 12px',
                  borderRadius: 10,
                  border: '1px solid rgba(255,255,255,.06)',
                  background: 'rgba(255,255,255,.015)',
                }}
              >
                <div
                  style={{
                    fontFamily: 'monospace',
                    fontSize: 9,
                    color: '#d9b45a',
                    marginBottom: 8,
                  }}
                >
                  {item.step}
                </div>

                <div
                  style={{
                    fontSize: 11,
                    color: '#ddd8cc',
                    marginBottom: 5,
                  }}
                >
                  {item.title}
                </div>

                <div
                  style={{
                    fontSize: 9,
                    lineHeight: 1.45,
                    color: 'rgba(255,255,255,.38)',
                  }}
                >
                  {item.detail}
                </div>
              </div>
            ))}
          </div>

          <div
            style={{
              marginTop: 14,
              display: 'grid',
              gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
              gap: 8,
            }}
          >
            {[
              ['Confidence gate', 'Low-confidence cases → review'],
              ['Retry limit', 'Bounded retries only'],
              ['Economic floor', 'Low-value actions → no action'],
              ['Idempotency', 'Duplicate recovery → blocked'],
            ].map(([title, detail]) => (
              <div
                key={title}
                style={{
                  padding: '11px 12px',
                  borderRadius: 9,
                  border: '1px solid rgba(217,180,90,.09)',
                  background: 'rgba(217,180,90,.025)',
                }}
              >
                <div
                  style={{
                    fontSize: 9,
                    textTransform: 'uppercase',
                    letterSpacing: '.08em',
                    color: '#d9b45a',
                  }}
                >
                  {title}
                </div>

                <div
                  style={{
                    marginTop: 5,
                    fontSize: 10,
                    lineHeight: 1.45,
                    color: 'rgba(255,255,255,.42)',
                  }}
                >
                  {detail}
                </div>
              </div>
            ))}
                      <div
            style={{
              marginTop: 14,
              padding: '15px 16px',
              borderRadius: 10,
              border: '1px solid rgba(255,255,255,.07)',
              background: 'rgba(255,255,255,.012)',
            }}
          >
            <div
              style={{
                fontSize: 10,
                letterSpacing: '.12em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,.38)',
                marginBottom: 12,
              }}
            >
              Stopping rules
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                gap: 10,
              }}
            >
              {[
                {
                  title: 'STOP — low confidence',
                  detail:
                    'If the diagnosis does not meet the confidence threshold, execution stops and the case is escalated.',
                },
                {
                  title: 'STOP — unsupported evidence',
                  detail:
                    'If the recommendation conflicts with failure evidence or retry history, no automated action is allowed.',
                },
                {
                  title: 'STOP — retry exhausted',
                  detail:
                    'If the transaction has reached its retry limit, OMNIX stops retrying and routes the case to manual review.',
                },
              ].map((rule) => (
                <div
                  key={rule.title}
                  style={{
                    padding: '12px',
                    borderRadius: 9,
                    border: '1px solid rgba(217,180,90,.1)',
                    background: 'rgba(217,180,90,.02)',
                  }}
                >
                  <div
                    style={{
                      fontSize: 9,
                      fontFamily: 'monospace',
                      color: '#d9b45a',
                      marginBottom: 7,
                    }}
                  >
                    {rule.title}
                  </div>

                  <div
                    style={{
                      fontSize: 10,
                      lineHeight: 1.55,
                      color: 'rgba(255,255,255,.42)',
                    }}
                  >
                    {rule.detail}
                  </div>
                </div>
              ))}
            </div>
          </div>
          </div>
        </section>
        {/* Footer note */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: 20,
            marginTop: 18,
            padding: '0 3px',
            color: 'rgba(255,255,255,.28)',
            fontSize: 10,
          }}
        >
          <span>
            Benchmark: {display.totalPayments.toLocaleString()} simulated
            payments · {display.groundTruthRecoverable} ground-truth recoverable
          </span>

          <span>
            {display.aiFalseApprovals} false automated approvals
          </span>
        </div>
      </div>
    </div>
  )
}