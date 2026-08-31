from dataclasses import dataclass

from .models import Payment
from .detector import RecoveryOpportunity
from .ai_diagnosis import AIDiagnosis
from .ai_diagnosis import ai_diagnose, convert_ai_diagnosis
from .evidence import EvidenceResult, verify_diagnosis
from .policy import PolicyDecision, evaluate_policy


@dataclass
class AICandidateResult:
    transaction_id: str
    diagnosis: AIDiagnosis
    evidence: EvidenceResult
    decision: PolicyDecision


def evaluate_ai_candidate(
    payment: Payment,
) -> AICandidateResult:

    opportunity = RecoveryOpportunity(
        transaction_id=payment.transaction_id,
        merchant_id=payment.merchant_id,
        amount=payment.amount,
        failure_code=payment.failure_code,
        failure_type=payment.failure_type,
        retry_count=payment.retry_count,
        payment_method=payment.payment_method,
        reason="AI candidate generated from failed payment",
    )

    diagnosis = ai_diagnose(
        payment,
        opportunity,
    )

    deterministic_diagnosis = convert_ai_diagnosis(
        diagnosis,
    )

    evidence = verify_diagnosis(
        opportunity,
        deterministic_diagnosis,
    )

    decision = evaluate_policy(
        opportunity,
        deterministic_diagnosis,
        evidence,
    )

    return AICandidateResult(
        transaction_id=payment.transaction_id,
        diagnosis=diagnosis,
        evidence=evidence,
        decision=decision,
    )