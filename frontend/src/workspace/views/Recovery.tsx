import { useMemo, useState } from 'react'

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

const BENCHMARK = {
  totalPayments: 1000,
  failedPayments: 252,
  groundTruthRecoverable: 112,
  baselineOpportunities: 81,
  aiCandidates: 171,
  aiApproved: 29,
  aiTrueApprovals: 29,
  aiFalseApprovals: 0,
  baselineRevenue: 942953.17,
  aiRevenue: 368643.46,
  combinedRevenue: 1311596.63,
  precision: 1.0,
  recall: 0.7232,
  f1: 0.8394,
  recoveryRate: 0.7026,
}

const SAMPLE_RECOVERIES: RecoveryRow[] = [
  {
    id: 'txn_000037',
    merchant: 'merchant_014',
    amount: 8922.01,
    method: 'wallet',
    failure: 'issuer_timeout',
    action: 'retry_payment',
    confidence: 0.94,
    status: 'Recovered',
  },
  {
    id: 'txn_000024',
    merchant: 'merchant_006',
    amount: 11397.72,
    method: 'netbanking',
    failure: 'timeout',
    action: 'retry_payment',
    confidence: 0.95,
    status: 'Recovered',
  },
  {
    id: 'txn_000030',
    merchant: 'merchant_011',
    amount: 14721.34,
    method: 'card',
    failure: 'network_error',
    action: 'retry_payment',
    confidence: 0.95,
    status: 'Recovered',
  },
  {
    id: 'txn_000046',
    merchant: 'merchant_003',
    amount: 12684.61,
    method: 'wallet',
    failure: 'issuer_timeout',
    action: 'retry_payment',
    confidence: 0.95,
    status: 'Recovered',
  },
  {
    id: 'txn_000065',
    merchant: 'merchant_009',
    amount: 13005.98,
    method: 'netbanking',
    failure: 'timeout',
    action: 'retry_payment',
    confidence: 0.95,
    status: 'Recovered',
  },
  {
    id: 'txn_000111',
    merchant: 'merchant_002',
    amount: 8842.73,
    method: 'card',
    failure: 'timeout',
    action: 'retry_payment',
    confidence: 0.95,
    status: 'Manual review',
  },
]

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
        background: 'linear-gradient(145deg, rgba(255,255,255,.035), rgba(255,255,255,.012))',
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
          background:
            recovered
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

  const rows = useMemo(() => {
    if (filter === 'recovered') {
      return SAMPLE_RECOVERIES.filter((row) => row.status === 'Recovered')
    }

    if (filter === 'review') {
      return SAMPLE_RECOVERIES.filter((row) => row.status === 'Manual review')
    }

    return SAMPLE_RECOVERIES
  }, [filter])

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
              OMNIX identifies failed payments, asks a reasoning model to
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
            ● Simulation benchmark
          </div>
        </div>

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
            value={money(BENCHMARK.baselineRevenue)}
            detail="Detected baseline opportunity"
            accent
          />

          <MetricCard
            label="AI incremental"
            value={money(BENCHMARK.aiRevenue)}
            detail="Additional approved recovery"
            accent
          />

          <MetricCard
            label="Combined recovered"
            value={money(BENCHMARK.combinedRevenue)}
            detail="Baseline + AI recovery"
            accent
          />

          <MetricCard
            label="Recovery rate"
            value={percent(BENCHMARK.recoveryRate)}
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
            value={BENCHMARK.totalPayments.toLocaleString()}
            detail={`${BENCHMARK.failedPayments} failed`}
          />

          <MetricCard
            label="Baseline opportunities"
            value={BENCHMARK.baselineOpportunities.toString()}
            detail="High-confidence detector output"
          />

          <MetricCard
            label="AI candidates"
            value={BENCHMARK.aiCandidates.toString()}
            detail={`${BENCHMARK.aiApproved} approved`}
          />

          <MetricCard
            label="False approvals"
            value={BENCHMARK.aiFalseApprovals.toString()}
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
                  {percent(BENCHMARK.precision)}
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
                  {percent(BENCHMARK.recall)}
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
                  {BENCHMARK.f1.toFixed(2)}
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
                  0
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
            Benchmark: 1,000 simulated payments · 112 ground-truth recoverable
          </span>

          <span>
            0 false automated approvals
          </span>
        </div>
      </div>
    </div>
  )
}