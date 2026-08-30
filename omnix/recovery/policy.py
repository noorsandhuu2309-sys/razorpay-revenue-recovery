from dataclasses import dataclass
from typing import List

from .detector import RecoveryOpportunity
from .diagnosis import Diagnosis
from .evidence import EvidenceResult


@dataclass
class PolicyDecision:
    transaction_id: str
    allowed: bool
    action: str
    reason: str
    rules_checked: List[str]


# Safety limits for automated recovery.
MAX_RETRY_COUNT = 0
MAX_AUTOMATIC_AMOUNT = 50000.00
MIN_CONFIDENCE = 0.80


def evaluate_policy(
    opportunity: RecoveryOpportunity,
    diagnosis: Diagnosis,
    evidence: EvidenceResult,
) -> PolicyDecision:
    """
    Deterministic authorization layer.

    The AI can recommend an action, but only this policy engine
    can authorize automated recovery.
    """

    rules_checked: List[str] = []

    # Rule 1: evidence must be verified.
    rules_checked.append("evidence_verified")

    if evidence.verdict != "verified":
        return PolicyDecision(
            transaction_id=opportunity.transaction_id,
            allowed=False,
            action="manual_review",
            reason="Evidence verification did not pass",
            rules_checked=rules_checked,
        )

    # Rule 2: confidence must meet the minimum threshold.
    rules_checked.append("confidence_threshold")

    if evidence.confidence < MIN_CONFIDENCE:
        return PolicyDecision(
            transaction_id=opportunity.transaction_id,
            allowed=False,
            action="manual_review",
            reason=(
                f"Evidence confidence {evidence.confidence:.2f} "
                f"is below required {MIN_CONFIDENCE:.2f}"
            ),
            rules_checked=rules_checked,
        )

    # Rule 3: transaction must not already have been retried.
    rules_checked.append("retry_limit")

    if opportunity.retry_count > MAX_RETRY_COUNT:
        return PolicyDecision(
            transaction_id=opportunity.transaction_id,
            allowed=False,
            action="manual_review",
            reason="Automatic retry limit has already been reached",
            rules_checked=rules_checked,
        )

    # Rule 4: only retry recommendations can be automated.
    rules_checked.append("allowed_action")

    if diagnosis.recommended_action != "retry_payment":
        return PolicyDecision(
            transaction_id=opportunity.transaction_id,
            allowed=False,
            action="manual_review",
            reason="Recommended action is not approved for automation",
            rules_checked=rules_checked,
        )

    # Rule 5: protect high-value transactions.
    rules_checked.append("amount_limit")

    if opportunity.amount > MAX_AUTOMATIC_AMOUNT:
        return PolicyDecision(
            transaction_id=opportunity.transaction_id,
            allowed=False,
            action="manual_review",
            reason=(
                f"Transaction amount ₹{opportunity.amount:.2f} "
                f"exceeds automatic limit ₹{MAX_AUTOMATIC_AMOUNT:.2f}"
            ),
            rules_checked=rules_checked,
        )

    # All safety rules passed.
    return PolicyDecision(
        transaction_id=opportunity.transaction_id,
        allowed=True,
        action="retry_payment",
        reason="All automated recovery policies passed",
        rules_checked=rules_checked,
    )


def evaluate_policies(
    opportunities: List[RecoveryOpportunity],
    diagnoses: List[Diagnosis],
    evidence_results: List[EvidenceResult],
) -> List[PolicyDecision]:

    diagnoses_by_id = {
        diagnosis.transaction_id: diagnosis
        for diagnosis in diagnoses
    }

    evidence_by_id = {
        evidence.transaction_id: evidence
        for evidence in evidence_results
    }

    decisions: List[PolicyDecision] = []

    for opportunity in opportunities:
        diagnosis = diagnoses_by_id.get(opportunity.transaction_id)
        evidence = evidence_by_id.get(opportunity.transaction_id)

        if diagnosis is None or evidence is None:
            continue

        decisions.append(
            evaluate_policy(
                opportunity,
                diagnosis,
                evidence,
            )
        )

    return decisions