from dataclasses import dataclass
from typing import List

from omnix.models import Capability, router

from .models import Payment
from .detector import RecoveryOpportunity


@dataclass
class AIDiagnosis:
    transaction_id: str
    diagnosis: str
    recommended_action: str
    confidence: float
    reasoning: str
    evidence: List[str]


SYSTEM_PROMPT = """
You are a payment recovery decision-support agent.

Your job is to diagnose failed payment transactions and recommend
a SAFE recovery action.

You are NOT allowed to execute payments.

Allowed actions:
- retry_payment
- change_payment_method
- manual_review
- no_action

Rules:
1. Transient failures such as timeouts may be retried.
2. Permanent failures such as invalid credentials or invalid payment
   details should NOT automatically be retried.
3. Repeated failures should increase caution.
4. Never invent payment facts.
5. Confidence must represent how strongly the supplied transaction
   evidence supports the diagnosis.
6. If evidence is insufficient, choose manual_review.
7. Return JSON only.

Return exactly:

{
  "diagnosis": "...",
  "recommended_action": "...",
  "confidence": 0.0,
  "reasoning": "...",
  "evidence": ["...", "..."]
}
"""


def ai_diagnose(
    payment: Payment,
    opportunity: RecoveryOpportunity,
) -> AIDiagnosis:

    user_prompt = f"""
Analyze this failed payment.

Transaction ID: {payment.transaction_id}
Merchant ID: {payment.merchant_id}
Amount: {payment.amount}
Currency: {payment.currency}
Payment method: {payment.payment_method}

Failure code: {payment.failure_code}
Failure type: {payment.failure_type.value}
Previous retry count: {payment.retry_count}

Recovery opportunity reason:
{opportunity.reason}

Return the required JSON object.
"""

    # OMNIX generate_json returns:
    # (parsed_data, RouterResult)
    data, result = router.generate_json(
        Capability.REASONING,
        system=SYSTEM_PROMPT,
        user=user_prompt,
        default=None,
    )

    if not result.ok or not isinstance(data, dict):
        return AIDiagnosis(
            transaction_id=payment.transaction_id,
            diagnosis="Unable to obtain reliable AI diagnosis",
            recommended_action="manual_review",
            confidence=0.0,
            reasoning=(
                "AI model was unavailable or returned invalid output."
            ),
            evidence=[],
        )

    action = str(
        data.get(
            "recommended_action",
            "manual_review",
        )
    )

    allowed_actions = {
        "retry_payment",
        "change_payment_method",
        "manual_review",
        "no_action",
    }

    if action not in allowed_actions:
        action = "manual_review"

    try:
        confidence = float(
            data.get(
                "confidence",
                0.0,
            )
        )
    except (TypeError, ValueError):
        confidence = 0.0

    # Keep confidence safely between 0 and 1.
    confidence = max(
        0.0,
        min(1.0, confidence),
    )

    evidence = data.get(
        "evidence",
        [],
    )

    if not isinstance(evidence, list):
        evidence = []

    evidence = [
        str(item)
        for item in evidence
        if item is not None
    ]

    return AIDiagnosis(
        transaction_id=payment.transaction_id,
        diagnosis=str(
            data.get(
                "diagnosis",
                "No reliable diagnosis provided",
            )
        ),
        recommended_action=action,
        confidence=confidence,
        reasoning=str(
            data.get(
                "reasoning",
                "",
            )
        ),
        evidence=evidence,
    )


def ai_diagnose_opportunities(
    payments: List[Payment],
    opportunities: List[RecoveryOpportunity],
) -> List[AIDiagnosis]:

    payment_by_id = {
        payment.transaction_id: payment
        for payment in payments
    }

    diagnoses: List[AIDiagnosis] = []

    for opportunity in opportunities:
        payment = payment_by_id.get(
            opportunity.transaction_id
        )

        if payment is None:
            continue

        diagnoses.append(
            ai_diagnose(
                payment,
                opportunity,
            )
        )

    return diagnoses
def convert_ai_diagnosis(
    diagnosis: AIDiagnosis,
):
    """
    Convert an AI diagnosis into the existing deterministic
    Diagnosis format used by the evidence and policy layers.
    """

    from .diagnosis import Diagnosis

    return Diagnosis(
        transaction_id=diagnosis.transaction_id,
        diagnosis=diagnosis.diagnosis,
        recommended_action=diagnosis.recommended_action,
        confidence=diagnosis.confidence,
        evidence=diagnosis.evidence,
    )