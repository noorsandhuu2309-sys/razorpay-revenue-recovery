from omnix.recovery.simulator import generate_payments
from omnix.recovery.detector import detect_recovery_opportunities
from omnix.recovery.diagnosis import Diagnosis
from omnix.recovery.evidence import verify_diagnosis
from omnix.recovery.policy import evaluate_policy


def test_unsupported_diagnosis_is_blocked():
    payments = generate_payments(1000)

    opportunities = detect_recovery_opportunities(payments)

    opportunity = opportunities[0]

    # Deliberately give the system a misleading diagnosis.
    bad_diagnosis = Diagnosis(
        transaction_id=opportunity.transaction_id,
        diagnosis="Permanent payment failure",
        recommended_action="retry_payment",
        confidence=0.99,
        evidence=[
            "AI claims this is permanent"
        ],
    )

    evidence = verify_diagnosis(
        opportunity,
        bad_diagnosis,
    )

    decision = evaluate_policy(
        opportunity,
        bad_diagnosis,
        evidence,
    )

    assert evidence.verdict != "verified"
    assert decision.allowed is False
    assert decision.action == "manual_review"
