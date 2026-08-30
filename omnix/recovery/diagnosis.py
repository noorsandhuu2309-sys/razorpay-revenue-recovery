from dataclasses import dataclass
from typing import List

from .detector import RecoveryOpportunity


@dataclass
class Diagnosis:
    transaction_id: str
    diagnosis: str
    recommended_action: str
    confidence: float
    evidence: List[str]


def diagnose_opportunity(
    opportunity: RecoveryOpportunity,
) -> Diagnosis:
    """
    Produce an explainable diagnosis for a recovery opportunity.

    This is the deterministic baseline. The AI/evidence layer
    will be added after this baseline is validated.
    """

    evidence: List[str] = []

    if opportunity.failure_code == "issuer_timeout":
        diagnosis = "Likely temporary issuer-side timeout"
        recommended_action = "retry_payment"
        confidence = 0.94

        evidence.extend([
            "Failure code indicates a timeout",
            "Timeouts are potentially transient",
            "No previous retry has been attempted",
        ])

    elif opportunity.failure_code == "network_error":
        diagnosis = "Likely temporary network failure"
        recommended_action = "retry_payment"
        confidence = 0.91

        evidence.extend([
            "Failure code indicates a network error",
            "Network failures can be transient",
            "No previous retry has been attempted",
        ])

    elif opportunity.failure_code == "timeout":
        diagnosis = "Likely temporary payment timeout"
        recommended_action = "retry_payment"
        confidence = 0.90

        evidence.extend([
            "Failure code indicates a timeout",
            "Timeout may recover on retry",
            "No previous retry has been attempted",
        ])

    else:
        diagnosis = "Cause requires further investigation"
        recommended_action = "manual_review"
        confidence = 0.50

        evidence.append(
            "Failure pattern does not meet automatic recovery criteria"
        )

    return Diagnosis(
        transaction_id=opportunity.transaction_id,
        diagnosis=diagnosis,
        recommended_action=recommended_action,
        confidence=confidence,
        evidence=evidence,
    )


def diagnose_opportunities(
    opportunities: List[RecoveryOpportunity],
) -> List[Diagnosis]:
    return [
        diagnose_opportunity(opportunity)
        for opportunity in opportunities
    ]