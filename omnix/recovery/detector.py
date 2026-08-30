from dataclasses import dataclass
from typing import List

from .models import Payment, PaymentStatus, FailureType


@dataclass
class RecoveryOpportunity:
    transaction_id: str
    merchant_id: str
    amount: float
    failure_code: str
    failure_type: FailureType
    retry_count: int
    payment_method: str
    reason: str


def detect_recovery_opportunities(
    payments: List[Payment],
) -> List[RecoveryOpportunity]:
    """
    Identify failed payments that appear potentially recoverable.

    This detector intentionally does NOT use the ground-truth
    `recoverable` field. That field exists only for evaluation.
    """

    opportunities: List[RecoveryOpportunity] = []

    for payment in payments:
        if payment.status != PaymentStatus.FAILED:
            continue

        # Transient failures with no previous retry are the
        # strongest initial recovery candidates.
        if (
            payment.failure_type == FailureType.TRANSIENT
            and payment.retry_count == 0
        ):
            reason = (
                f"Transient failure ({payment.failure_code}) "
                "with no previous retry"
            )

            opportunities.append(
                RecoveryOpportunity(
                    transaction_id=payment.transaction_id,
                    merchant_id=payment.merchant_id,
                    amount=payment.amount,
                    failure_code=payment.failure_code,
                    failure_type=payment.failure_type,
                    retry_count=payment.retry_count,
                    payment_method=payment.payment_method,
                    reason=reason,
                )
            )

    return opportunities