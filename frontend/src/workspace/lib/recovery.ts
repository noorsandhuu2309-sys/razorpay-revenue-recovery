/** Shared recovery benchmark data for Revora views. */
import { create, type StoreApi, type UseBoundStore } from 'zustand'

export type RecoveryStatus = 'Recovered' | 'Manual review' | 'Blocked'

export interface RecoveryAuditRecord {
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
  status: RecoveryStatus
}

export interface RecoveryAIResult {
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

let cached: Promise<RecoveryAIResult> | null = null

export function loadRecoveryData(): Promise<RecoveryAIResult> {
  if (!cached) {
    cached = fetch('/api/recovery/ai').then(async (response) => {
      if (!response.ok) {
        throw new Error(`Recovery API returned ${response.status}`)
      }
      return (await response.json()) as RecoveryAIResult
    })
  }
  return cached
}

export function resetRecoveryCache(): void {
  cached = null
}

/**
 * The recovery run is a single, batch-scoped source of truth.  Keeping it
 * outside individual screens prevents StrictMode remounts and navigation from
 * starting duplicate benchmark requests, while the selected case survives a
 * move from the queue into evidence, policy, or audit views.
 */
interface RecoveryState {
  data: RecoveryAIResult | null
  loading: boolean
  error: string | null
  selectedTransactionId: string | null
  queueStatus: string
  load: () => Promise<void>
  refresh: () => Promise<void>
  selectCase: (transactionId: string | null) => void
  setQueueStatus: (status: string) => void
}

let storeRequest: Promise<void> | null = null

export const useRecoveryData: UseBoundStore<StoreApi<RecoveryState>> = create<RecoveryState>((set) => ({
  data: null,
  loading: false,
  error: null,
  selectedTransactionId: null,
  queueStatus: 'all',
  async load() {
    if (storeRequest) return storeRequest
    set({ loading: true, error: null })
    storeRequest = loadRecoveryData()
      .then((data) => set((state) => ({
        data,
        loading: false,
        // A case becomes selected only through an explicit user action. On a
        // refresh, retain it only when that transaction still exists in the
        // new batch; case-aware views can then show their safe empty state
        // rather than dereferencing a stale record.
        selectedTransactionId: data.audit_trail.some(
          (record) => record.transaction_id === state.selectedTransactionId,
        ) ? state.selectedTransactionId : null,
      })))
      .catch((error: unknown) => set({
        loading: false,
        error: error instanceof Error ? error.message : 'Unable to load recovery data.',
      }))
      .finally(() => { storeRequest = null })
    return storeRequest
  },
  async refresh(): Promise<void> {
    // Do not invalidate an in-flight request: starting another one here lets
    // the older response win later and restore stale data/selection.
    if (storeRequest) {
      await storeRequest
      return
    }
    resetRecoveryCache()
    await useRecoveryData.getState().load()
  },
  selectCase: (selectedTransactionId) => set({ selectedTransactionId }),
  setQueueStatus: (queueStatus) => set({ queueStatus }),
}))

export function selectedRecoveryCase(state: Pick<RecoveryState, 'data' | 'selectedTransactionId'>): RecoveryAuditRecord | null {
  return state.data?.audit_trail.find((record) => record.transaction_id === state.selectedTransactionId)
    ?? null
}

export function money(value: number): string {
  return `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

export function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

export function recoveryMetrics(data: RecoveryAIResult) {
  const precision =
    data.ai_approved > 0
      ? data.ai_successful_recoveries / data.ai_approved
      : 0
  const aiRecall =
    data.ground_truth_recoverable > 0
      ? data.ai_successful_recoveries / data.ground_truth_recoverable
      : 0
  const f1 =
    precision + aiRecall > 0
      ? (2 * precision * aiRecall) / (precision + aiRecall)
      : 0

  return {
    totalPayments: data.total_payments,
    failedPayments: data.failed_payments,
    revenueAtRisk: data.revenue_at_risk,
    baselineRevenue: data.baseline_revenue,
    baselineOpportunities: data.baseline_opportunities,
    aiCandidates: data.ai_candidates,
    aiApproved: data.ai_approved,
    aiRecovered: data.ai_successful_recoveries,
    aiRevenue: data.ai_revenue,
    combinedRevenue: data.combined_revenue,
    recoveryRate: data.recovery_rate,
    falseApprovals: data.false_approvals,
    groundTruthRecoverable: data.ground_truth_recoverable,
    precision,
    aiRecall,
    f1,
  }
}
