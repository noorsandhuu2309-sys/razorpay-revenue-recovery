from dataclasses import dataclass
from typing import List

from .models import Payment, PaymentStatus
from .detector import (
    RecoveryOpportunity,
    detect_recovery_opportunities,
)
from .ai_candidate import (
    AICandidateResult,
    evaluate_ai_candidate,
)
from .executor import RecoveryResult, execute_recoveries


@dataclass
class EnhancedRecoveryResult:
    baseline_opportunities: List[RecoveryOpportunity]
    ai_candidates: List[AICandidateResult]
    ai_approved: List[AICandidateResult]
    all_results: List[RecoveryResult]


def run_enhanced_recovery(
    payments: List[Payment],
    max_ai_candidates: int | None = None,
) -> EnhancedRecoveryResult:

    # ---------------------------------------------------------
    # Stage 1: existing deterministic detector.
    # ---------------------------------------------------------
    baseline_opportunities = detect_recovery_opportunities(
        payments
    )

    baseline_ids = {
        opportunity.transaction_id
        for opportunity in baseline_opportunities
    }

    # ---------------------------------------------------------
    # Stage 2: AI evaluates only failed payments missed
    # by the deterministic detector.
    # ---------------------------------------------------------
    missed_payments = [
        payment
        for payment in payments
        if (
            payment.status == PaymentStatus.FAILED
            and payment.transaction_id not in baseline_ids
        )
    ]

    if max_ai_candidates is not None:
        missed_payments = missed_payments[:max_ai_candidates]

    ai_candidates: List[AICandidateResult] = []

    for payment in missed_payments:
        ai_candidates.append(
            evaluate_ai_candidate(payment)
        )

    # ---------------------------------------------------------
    # Stage 3: only policy-approved AI candidates proceed.
    # ---------------------------------------------------------
    ai_approved = [
        candidate
        for candidate in ai_candidates
        if candidate.decision.allowed
    ]

    # ---------------------------------------------------------
    # Stage 4: execute AI-approved recoveries.
    #
    # The executor is still the final action simulator.
    # ---------------------------------------------------------
    approved_opportunities = [
        RecoveryOpportunity(
            transaction_id=candidate.transaction_id,
            merchant_id=next(
                payment.merchant_id
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            amount=next(
                payment.amount
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            failure_code=next(
                payment.failure_code
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            failure_type=next(
                payment.failure_type
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            retry_count=next(
                payment.retry_count
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            payment_method=next(
                payment.payment_method
                for payment in payments
                if payment.transaction_id
                == candidate.transaction_id
            ),
            reason="AI diagnosis passed evidence and policy",
        )
        for candidate in ai_approved
    ]

    # Build decisions from the AI candidates.
    decisions = [
        candidate.decision
        for candidate in ai_approved
    ]

    ai_results = execute_recoveries(
        payments,
        approved_opportunities,
        decisions,
    )

    return EnhancedRecoveryResult(
        baseline_opportunities=baseline_opportunities,
        ai_candidates=ai_candidates,
        ai_approved=ai_approved,
        all_results=ai_results,
    )