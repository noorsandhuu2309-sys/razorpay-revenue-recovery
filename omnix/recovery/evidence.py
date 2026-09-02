from dataclasses import dataclass
from typing import List

from .detector import RecoveryOpportunity
from .diagnosis import Diagnosis


@dataclass
class EvidenceResult:
    transaction_id: str
    verified: bool
    verdict: str
    confidence: float
    evidence: List[str]


# Maximum number of retries that evidence can support.
MAX_EVIDENCE_RETRIES = 2


def verify_diagnosis(
    opportunity: RecoveryOpportunity,
    diagnosis: Diagnosis,
) -> EvidenceResult:
    """
    Deterministically verify whether the recommended recovery action
    is supported by the actual transaction evidence.

    The AI diagnosis is advisory only. Transaction fields are the
    source of truth for authorization.
    """

    evidence: List[str] = []
    checks_passed = 0
    checks_total = 0

    # ---------------------------------------------------------
    # Check 1: failure type supports the diagnosis/action.
    # ---------------------------------------------------------
    checks_total += 1

    if (
        opportunity.failure_type.value == "transient"
        and diagnosis.recommended_action == "retry_payment"
    ):
        checks_passed += 1
        evidence.append(
            "Failure type confirms a transient payment failure"
        )
    elif opportunity.failure_type.value == "permanent":
        evidence.append(
            "Permanent failure does not support automatic retry"
        )
    else:
        evidence.append(
            "Failure type does not support automatic retry"
        )

    # ---------------------------------------------------------
    # Check 2: failure code is a known retryable transient code.
    # ---------------------------------------------------------
    checks_total += 1

    retryable_codes = {
        "issuer_timeout",
        "timeout",
        "network_error",
    }

    if (
        opportunity.failure_type.value == "transient"
        and opportunity.failure_code in retryable_codes
        and diagnosis.recommended_action == "retry_payment"
    ):
        checks_passed += 1
        evidence.append(
            f"Failure code '{opportunity.failure_code}' "
            "supports retry"
        )
    else:
        evidence.append(
            f"Failure code '{opportunity.failure_code}' "
            "does not support automatic retry"
        )

    # ---------------------------------------------------------
    # Check 3: retry count is within the safe evidence limit.
    # ---------------------------------------------------------
    checks_total += 1

    if opportunity.retry_count < MAX_EVIDENCE_RETRIES:
        checks_passed += 1
        evidence.append(
            f"Retry count ({opportunity.retry_count}) "
            f"is within the safe limit of "
            f"{MAX_EVIDENCE_RETRIES}"
        )
    else:
        evidence.append(
            f"Retry count ({opportunity.retry_count}) "
            f"has reached the safe limit of "
            f"{MAX_EVIDENCE_RETRIES}"
        )

    # ---------------------------------------------------------
    # Calculate deterministic verification score.
    # ---------------------------------------------------------
    verification_score = (
        checks_passed / checks_total
        if checks_total > 0
        else 0.0
    )

    if verification_score == 1.0:
        verdict = "verified"
    elif verification_score >= 0.66:
        verdict = "weak"
    else:
        verdict = "unsupported"

    # AI confidence is weighted by deterministic evidence support.
    confidence = round(
        verification_score * diagnosis.confidence,
        2,
    )

    return EvidenceResult(
        transaction_id=opportunity.transaction_id,
        verified=verdict == "verified",
        verdict=verdict,
        confidence=confidence,
        evidence=evidence,
    )


def verify_opportunities(
    opportunities: List[RecoveryOpportunity],
    diagnoses: List[Diagnosis],
) -> List[EvidenceResult]:
    """
    Verify a collection of recovery opportunities against
    their corresponding diagnoses.
    """

    results: List[EvidenceResult] = []

    diagnosis_by_transaction = {
        diagnosis.transaction_id: diagnosis
        for diagnosis in diagnoses
    }

    for opportunity in opportunities:
        diagnosis = diagnosis_by_transaction.get(
            opportunity.transaction_id
        )

        if diagnosis is None:
            continue

        results.append(
            verify_diagnosis(
                opportunity,
                diagnosis,
            )
        )

    return results