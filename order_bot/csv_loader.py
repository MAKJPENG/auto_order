from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

from .models import Order, split_product_urls
from .time_utils import parse_datetime, resolve_order_timezone, timezone_label


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


def load_orders(path: Path, tz, *, use_country_timezone: bool = False) -> list[Order]:
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
            row_tz = _row_timezone(normalized, line_number, tz, use_country_timezone)
            orders.append(_parse_order(normalized, line_number, row_tz))

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

    product_urls = split_product_urls(row["product_url"])
    if not product_urls:
        raise ValueError(f"Line {line_number}: product_url is required.")
    for product_url in product_urls:
        parsed_url = urlparse(product_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"Line {line_number}: product_url must contain http(s) URL values.")

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
        phone=row.get("phone", ""),
        time_zone=timezone_label(tz),
        raw=row,
    )


def _row_timezone(row: dict[str, str], line_number: int, default_tz, use_country_timezone: bool):
    if not use_country_timezone:
        return default_tz
    try:
        return resolve_order_timezone(
            country=row.get("country", ""),
            country_code=row.get("country_code", ""),
            timezone_name=row.get("timezone", "") or row.get("time_zone", ""),
            default_tz=default_tz,
        )
    except ValueError as exc:
        raise ValueError(f"Line {line_number}: {exc}") from exc
