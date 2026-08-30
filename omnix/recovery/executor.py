from dataclasses import dataclass
from typing import List

from .detector import RecoveryOpportunity
from .models import Payment
from .policy import PolicyDecision


@dataclass
class RecoveryResult:
    transaction_id: str
    attempted: bool
    success: bool
    amount_recovered: float
    action: str
    message: str


def execute_recovery(
    payment: Payment,
    opportunity: RecoveryOpportunity,
    decision: PolicyDecision,
) -> RecoveryResult:
    """
    Simulate a recovery action.

    No real payment is processed.

    The simulator uses the payment's ground-truth recoverability
    only to evaluate whether the recovery attempt would succeed.
    """

    if not decision.allowed:
        return RecoveryResult(
            transaction_id=payment.transaction_id,
            attempted=False,
            success=False,
            amount_recovered=0.0,
            action=decision.action,
            message="Recovery blocked by policy",
        )

    if decision.action != "retry_payment":
        return RecoveryResult(
            transaction_id=payment.transaction_id,
            attempted=False,
            success=False,
            amount_recovered=0.0,
            action=decision.action,
            message="Action is not executable",
        )

    if payment.recoverable:
        return RecoveryResult(
            transaction_id=payment.transaction_id,
            attempted=True,
            success=True,
            amount_recovered=payment.amount,
            action="retry_payment",
            message="Simulated retry succeeded",
        )

    return RecoveryResult(
        transaction_id=payment.transaction_id,
        attempted=True,
        success=False,
        amount_recovered=0.0,
        action="retry_payment",
        message="Simulated retry failed",
    )


def execute_recoveries(
    payments: List[Payment],
    opportunities: List[RecoveryOpportunity],
    decisions: List[PolicyDecision],
) -> List[RecoveryResult]:

    payments_by_id = {
        payment.transaction_id: payment
        for payment in payments
    }

    opportunities_by_id = {
        opportunity.transaction_id: opportunity
        for opportunity in opportunities
    }

    results: List[RecoveryResult] = []

    for decision in decisions:
        payment = payments_by_id.get(decision.transaction_id)
        opportunity = opportunities_by_id.get(decision.transaction_id)

        if payment is None or opportunity is None:
            continue

        results.append(
            execute_recovery(
                payment,
                opportunity,
                decision,
            )
        )

    return results