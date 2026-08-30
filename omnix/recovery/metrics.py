from dataclasses import dataclass
from typing import List

from .detector import RecoveryOpportunity
from .executor import RecoveryResult
from .models import Payment, PaymentStatus


@dataclass
class RecoveryMetrics:
    total_payments: int
    failed_payments: int
    ground_truth_recoverable: int
    detected_opportunities: int

    true_positives: int
    false_positives: int
    false_negatives: int

    precision: float
    recall: float
    f1: float

    revenue_at_risk: float
    revenue_recovered: float
    recovery_rate: float

    automated_attempts: int
    successful_recoveries: int


def calculate_metrics(
    payments: List[Payment],
    opportunities: List[RecoveryOpportunity],
    recovery_results: List[RecoveryResult],
) -> RecoveryMetrics:

    failed_payments = [
        payment
        for payment in payments
        if payment.status == PaymentStatus.FAILED
    ]

    ground_truth_recoverable = [
        payment
        for payment in failed_payments
        if payment.recoverable
    ]

    detected_ids = {
        opportunity.transaction_id
        for opportunity in opportunities
    }

    recoverable_ids = {
        payment.transaction_id
        for payment in ground_truth_recoverable
    }

    true_positives = len(detected_ids & recoverable_ids)
    false_positives = len(detected_ids - recoverable_ids)
    false_negatives = len(recoverable_ids - detected_ids)

    precision = (
        true_positives / len(detected_ids)
        if detected_ids
        else 0.0
    )

    recall = (
        true_positives / len(recoverable_ids)
        if recoverable_ids
        else 0.0
    )

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )

    revenue_at_risk = sum(
        payment.amount
        for payment in ground_truth_recoverable
    )

    revenue_recovered = sum(
        result.amount_recovered
        for result in recovery_results
    )

    recovery_rate = (
        revenue_recovered / revenue_at_risk
        if revenue_at_risk > 0
        else 0.0
    )

    automated_attempts = sum(
        result.attempted
        for result in recovery_results
    )

    successful_recoveries = sum(
        result.success
        for result in recovery_results
    )

    return RecoveryMetrics(
        total_payments=len(payments),
        failed_payments=len(failed_payments),
        ground_truth_recoverable=len(ground_truth_recoverable),
        detected_opportunities=len(opportunities),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        revenue_at_risk=round(revenue_at_risk, 2),
        revenue_recovered=round(revenue_recovered, 2),
        recovery_rate=round(recovery_rate, 4),
        automated_attempts=automated_attempts,
        successful_recoveries=successful_recoveries,
    )