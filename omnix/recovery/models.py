from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class PaymentStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class FailureType(str, Enum):
    NONE = "none"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RISK = "risk"
    UNKNOWN = "unknown"


@dataclass
class Payment:
    transaction_id: str
    merchant_id: str
    amount: float
    currency: str
    payment_method: str
    status: PaymentStatus
    failure_code: str
    failure_type: FailureType
    retry_count: int
    timestamp: datetime
    recoverable: bool