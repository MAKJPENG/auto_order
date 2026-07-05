from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Order:
    order_id: str
    run_at: datetime | None
    email: str
    product_url: str
    quantity: int
    full_name: str
    country: str
    address_line: str
    city: str
    postal_code: str
    payment_method: str
    notes: str
    phone: str = ""
    time_zone: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def first_name(self) -> str:
        if self.raw.get("first_name"):
            return self.raw["first_name"]
        parts = self.full_name.split()
        return parts[0] if parts else self.full_name

    @property
    def last_name(self) -> str:
        if self.raw.get("last_name"):
            return self.raw["last_name"]
        parts = self.full_name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else self.full_name

    def value(self, key: str, default: str = "") -> str:
        return self.raw.get(key, default)


@dataclass(frozen=True)
class ScheduleEntry:
    order: Order
    scheduled_at: datetime
    source: str


@dataclass(frozen=True)
class OrderAttemptResult:
    success: bool
    submitted: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
