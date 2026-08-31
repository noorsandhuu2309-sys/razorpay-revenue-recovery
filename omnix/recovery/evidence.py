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
    Deterministically verify whether the diagnosis is supported
    by the transaction evidence.

    The model/diagnosis does not get to verify itself.
    """

    evidence: List[str] = []
    checks_passed = 0
    checks_total = 0

    diagnosis_lower = diagnosis.diagnosis.lower()

    # ---------------------------------------------------------
    # Check 1: diagnosis matches the actual failure type.
    # ---------------------------------------------------------
    checks_total += 1

    transient_terms = {
        "temporary",
        "transient",
        "retryable",
        "retry",
    }

    if (
        opportunity.failure_type.value == "transient"
        and any(
            term in diagnosis_lower
            for term in transient_terms
        )
    ):
        checks_passed += 1
        evidence.append(
            "Failure type confirms a transient payment failure"
        )
    else:
        evidence.append(
            "Failure type does not fully support the diagnosis"
        )

    # ---------------------------------------------------------
    # Check 2: failure code is consistent with the diagnosis.
    # ---------------------------------------------------------
    checks_total += 1

    timeout_codes = {
        "issuer_timeout",
        "timeout",
    }

    network_codes = {
        "network_error",
    }

    code_supported = (
        opportunity.failure_code in timeout_codes
        and "timeout" in diagnosis_lower
    ) or (
        opportunity.failure_code in network_codes
        and "network" in diagnosis_lower
    )

    if code_supported:
        checks_passed += 1
        evidence.append(
            f"Failure code '{opportunity.failure_code}' "
            "supports the diagnosis"
        )
    else:
        evidence.append(
            f"Failure code '{opportunity.failure_code}' "
            "does not fully support the diagnosis"
        )

    # ---------------------------------------------------------
    # Check 3: retry count is within the safe evidence limit.
    # ---------------------------------------------------------
    checks_total += 1

    if (
        opportunity.retry_count
        < MAX_EVIDENCE_RETRIES
    ):
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

    # IMPORTANT:
    # The model's confidence is NOT trusted by itself.
    # It is multiplied by deterministic evidence support.
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