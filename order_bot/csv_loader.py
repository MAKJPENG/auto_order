from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

from .models import Order
from .time_utils import parse_datetime


REQUIRED_COLUMNS = {
    "order_id",
    "email",
    "product_url",
    "quantity",
    "full_name",
    "country",
    "address_line",
    "city",
    "postal_code",
}


def load_orders(path: Path, tz) -> list[Order]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        columns = {name.strip() for name in reader.fieldnames if name}
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"CSV missing required columns: {', '.join(missing)}")

        orders: list[Order] = []
        for line_number, row in enumerate(reader, start=2):
            normalized = _normalize_row(row)
            if not any(normalized.values()):
                continue
            orders.append(_parse_order(normalized, line_number, tz))

    if not orders:
        raise ValueError("CSV does not contain any orders.")
    return orders


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    return {
        (key or "").strip(): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def _parse_order(row: dict[str, str], line_number: int, tz) -> Order:
    for column in REQUIRED_COLUMNS:
        if not row.get(column):
            raise ValueError(f"Line {line_number}: {column} is required.")

    try:
        quantity = int(row["quantity"])
    except ValueError as exc:
        raise ValueError(f"Line {line_number}: quantity must be an integer.") from exc
    if quantity < 1:
        raise ValueError(f"Line {line_number}: quantity must be greater than 0.")

    parsed_url = urlparse(row["product_url"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError(f"Line {line_number}: product_url must be an http(s) URL.")

    row.setdefault("payment_method", "bank_transfer")
    if not row["payment_method"]:
        row["payment_method"] = "bank_transfer"

    return Order(
        order_id=row["order_id"],
        run_at=parse_datetime(row.get("run_at", ""), tz),
        email=row["email"],
        product_url=row["product_url"],
        quantity=quantity,
        full_name=row["full_name"],
        country=row["country"],
        address_line=row["address_line"],
        city=row["city"],
        postal_code=row["postal_code"],
        payment_method=row["payment_method"],
        notes=row.get("notes", ""),
        raw=row,
    )
