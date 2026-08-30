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

    # Check 1: diagnosis matches the actual failure type.
    checks_total += 1

    if (
        opportunity.failure_type.value == "transient"
        and "temporary" in diagnosis.diagnosis.lower()
    ):
        checks_passed += 1
        evidence.append(
            "Failure type confirms a transient payment failure"
        )
    else:
        evidence.append(
            "Failure type does not fully support the diagnosis"
        )

    # Check 2: failure code is consistent with the diagnosis.
    checks_total += 1

    timeout_codes = {
        "issuer_timeout",
        "timeout",
    }

    network_codes = {
        "network_error",
    }

    diagnosis_lower = diagnosis.diagnosis.lower()

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

    # Check 3: no previous retry.
    checks_total += 1

    if opportunity.retry_count == 0:
        checks_passed += 1
        evidence.append(
            "No previous retry has been attempted"
        )
    else:
        evidence.append(
            "A previous retry has already been attempted"
        )

    verification_score = checks_passed / checks_total

    if verification_score == 1.0:
        verdict = "verified"
    elif verification_score >= 0.66:
        verdict = "weak"
    else:
        verdict = "unsupported"

    # Confidence is derived from deterministic verification,
    # not from the diagnosis model's self-reported confidence.
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
    results = []

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