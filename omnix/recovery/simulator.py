import random
from datetime import datetime, timedelta
from typing import List

from .models import Payment, PaymentStatus, FailureType


FAILURE_PROFILES = [
    {
        "code": "issuer_timeout",
        "type": FailureType.TRANSIENT,
        "recoverable": True,
        "weight": 15,
    },
    {
        "code": "network_error",
        "type": FailureType.TRANSIENT,
        "recoverable": True,
        "weight": 12,
    },
    {
        "code": "insufficient_funds",
        "type": FailureType.PERMANENT,
        "recoverable": False,
        "weight": 20,
    },
    {
        "code": "card_expired",
        "type": FailureType.PERMANENT,
        "recoverable": False,
        "weight": 8,
    },
    {
        "code": "risk_block",
        "type": FailureType.RISK,
        "recoverable": False,
        "weight": 5,
    },
    {
        "code": "timeout",
        "type": FailureType.TRANSIENT,
        "recoverable": True,
        "weight": 10,
    },
    {
        "code": "user_cancelled",
        "type": FailureType.PERMANENT,
        "recoverable": False,
        "weight": 10,
    },
]


PAYMENT_METHODS = [
    "upi",
    "card",
    "netbanking",
    "wallet",
]


def generate_payments(
    count: int = 1000,
    seed: int = 42,
) -> List[Payment]:
    """
    Generate a deterministic synthetic payment dataset.

    The seed makes experiments reproducible, while the recoverable
    field provides ground truth for evaluating our AI later.
    """
    rng = random.Random(seed)

    payments: List[Payment] = []

    start_time = datetime.now() - timedelta(days=30)

    failure_choices = [
        profile
        for profile in FAILURE_PROFILES
        for _ in range(profile["weight"])
    ]

    for index in range(count):
        transaction_id = f"txn_{index + 1:06d}"
        merchant_id = f"merchant_{rng.randint(1, 20):03d}"

        amount = round(rng.uniform(100, 25000), 2)

        payment_method = rng.choice(PAYMENT_METHODS)

        timestamp = start_time + timedelta(
            seconds=rng.randint(0, 30 * 24 * 60 * 60)
        )

        # Roughly 75% successful payments.
        if rng.random() < 0.75:
            payment = Payment(
                transaction_id=transaction_id,
                merchant_id=merchant_id,
                amount=amount,
                currency="INR",
                payment_method=payment_method,
                status=PaymentStatus.SUCCESS,
                failure_code="none",
                failure_type=FailureType.NONE,
                retry_count=0,
                timestamp=timestamp,
                recoverable=False,
            )
        else:
            profile = rng.choice(failure_choices)

            retry_count = rng.choices(
                [0, 1, 2],
                weights=[70, 25, 5],
                k=1,
            )[0]

            # A payment with retries already attempted is harder to recover.
            recoverable = profile["recoverable"] and retry_count < 2

            payment = Payment(
                transaction_id=transaction_id,
                merchant_id=merchant_id,
                amount=amount,
                currency="INR",
                payment_method=payment_method,
                status=PaymentStatus.FAILED,
                failure_code=profile["code"],
                failure_type=profile["type"],
                retry_count=retry_count,
                timestamp=timestamp,
                recoverable=recoverable,
            )

        payments.append(payment)

    return payments