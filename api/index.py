from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from omnix.recovery.simulator import generate_payments
from omnix.recovery.experiment import run_enhanced_recovery


app = FastAPI(title="OMNIX")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


ROOT_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = ROOT_DIR / "index.html"


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "OMNIX",
    }


@app.get("/api/recovery")
async def recovery():
    """
    Run the revenue recovery benchmark and return
    structured results for the Recovery dashboard.
    """

    payments = generate_payments(1000)
    result = run_enhanced_recovery(payments)

    baseline_ids = {
        opportunity.transaction_id
        for opportunity in result.baseline_opportunities
    }

    payment_by_id = {
        payment.transaction_id: payment
        for payment in payments
    }

    baseline_revenue = sum(
        payment.amount
        for payment in payments
        if payment.transaction_id in baseline_ids
        and payment.recoverable
    )

    ai_revenue = sum(
        recovery.amount_recovered
        for recovery in result.all_results
        if recovery.success
    )

    ai_true_approvals = sum(
        1
        for candidate in result.ai_approved
        if payment_by_id.get(candidate.transaction_id)
        and payment_by_id[candidate.transaction_id].recoverable
    )

    ai_false_approvals = sum(
        1
        for candidate in result.ai_approved
        if payment_by_id.get(candidate.transaction_id)
        and not payment_by_id[candidate.transaction_id].recoverable
    )

    ground_truth_recoverable = sum(
        1
        for payment in payments
        if payment.recoverable
    )

    revenue_at_risk = sum(
        payment.amount
        for payment in payments
        if payment.recoverable
    )

    combined_revenue = baseline_revenue + ai_revenue

    recovery_rate = (
        combined_revenue / revenue_at_risk
        if revenue_at_risk
        else 0.0
    )

    return {
        "status": "ok",
        "benchmark": {
            "total_payments": len(payments),
            "failed_payments": sum(
                1
                for payment in payments
                if payment.status.value == "failed"
            ),
            "ground_truth_recoverable": ground_truth_recoverable,
            "baseline_opportunities": len(
                result.baseline_opportunities
            ),
            "ai_candidates": len(result.ai_candidates),
            "ai_approved": len(result.ai_approved),
            "ai_true_approvals": ai_true_approvals,
            "ai_false_approvals": ai_false_approvals,
            "revenue_at_risk": round(
                revenue_at_risk,
                2,
            ),
            "baseline_revenue": round(
                baseline_revenue,
                2,
            ),
            "ai_revenue": round(
                ai_revenue,
                2,
            ),
            "combined_revenue": round(
                combined_revenue,
                2,
            ),
            "recovery_rate": round(
                recovery_rate,
                4,
            ),
        },
    }


@app.get("/")
async def root():
    if INDEX_FILE.exists():
        return HTMLResponse(
            content=INDEX_FILE.read_text(
                encoding="utf-8"
            )
        )

    return JSONResponse(
        status_code=404,
        content={
            "error": "index.html not found"
        },
    )


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if INDEX_FILE.exists():
        return HTMLResponse(
            content=INDEX_FILE.read_text(
                encoding="utf-8"
            )
        )

    return JSONResponse(
        status_code=404,
        content={
            "error": "index.html not found"
        },
    )