from dataclasses import dataclass
from typing import List

from .models import Payment, PaymentStatus
from .detector import RecoveryOpportunity
from .ai_diagnosis import AIDiagnosis, ai_diagnose


@dataclass
class AIEvaluationResult:
    transaction_id: str
    diagnosis: AIDiagnosis
    ground_truth_recoverable: bool


def evaluate_missed_payments(
    payments: List[Payment],
    existing_opportunities: List[RecoveryOpportunity],
) -> List[AIEvaluationResult]:

    existing_ids = {
        opportunity.transaction_id
        for opportunity in existing_opportunities
    }

    missed_payments = [
        payment
        for payment in payments
        if (
            payment.status == PaymentStatus.FAILED
            and payment.transaction_id not in existing_ids
        )
    ]

    results: List[AIEvaluationResult] = []

    for payment in missed_payments:

        # Construct a minimal opportunity-like object so that
        # the AI sees the same failure information.
        opportunity = RecoveryOpportunity(
            transaction_id=payment.transaction_id,
            merchant_id=payment.merchant_id,
            amount=payment.amount,
            failure_code=payment.failure_code,
            failure_type=payment.failure_type,
            retry_count=payment.retry_count,
            payment_method=payment.payment_method,
            reason="Payment failure not selected by deterministic detector",
        )

        diagnosis = ai_diagnose(
            payment,
            opportunity,
        )

        results.append(
            AIEvaluationResult(
                transaction_id=payment.transaction_id,
                diagnosis=diagnosis,
                ground_truth_recoverable=payment.recoverable,
            )
        )

    return results