from __future__ import annotations

import unittest
from datetime import datetime, timezone

from order_bot.gui import FAILED_ROW_TAG, ERROR_LOG_TAG, OrderBotApp, RowState, STATUS_DONE, STATUS_FAILED, STATUS_PENDING, parse_gui_args
from order_bot.models import Order, ScheduleEntry


class FakeTable:
    def __init__(self):
        self.updated_items: list[str] = []
        self.last_tags: tuple[str, ...] = ()

    def item(self, item_id, values=None, tags=()):
        self.updated_items.append(item_id)
        self.last_tags = tags

    def heading(self, column, option=None):
        return {"status": "状态", "message": "执行信息", "order_id": "order_id"}.get(column, column)


class FakeLog:
    def __init__(self):
        self.inserted: list[tuple[str, str | None]] = []

    def configure(self, **_kwargs):
        pass

    def insert(self, _index, text, tag=None):
        self.inserted.append((text, tag))

    def see(self, _index):
        pass


class GuiRowUpdateTests(unittest.TestCase):
    def test_parse_gui_args_ignores_macos_finder_process_serial_number(self):
        args = parse_gui_args(["--self-test", "-psn_0_12345"])

        self.assertTrue(args.self_test)

    def make_entry(self, order_id: str) -> ScheduleEntry:
        order = Order(
            order_id=order_id,
            run_at=None,
            email="buyer@example.com",
            product_url="https://example.com/product",
            quantity=1,
            full_name="Test Buyer",
            country="United Kingdom",
            address_line="1 Test Street",
            city="Birmingham",
            postal_code="B1 1BA",
            payment_method="bank_transfer",
            notes="",
            raw={"order_id": order_id},
        )
        return ScheduleEntry(
            order=order,
            scheduled_at=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
            source="run_at",
        )

    def test_duplicate_order_ids_update_by_row_key(self):
        app = OrderBotApp.__new__(OrderBotApp)
        app.table = FakeTable()
        app.table_columns = ["status", "message", "order_id"]
        app.tz = timezone.utc
        app.rows = [
            RowState(entry=self.make_entry("duplicate"), item_id="item-0", row_key="row-0"),
            RowState(entry=self.make_entry("duplicate"), item_id="item-1", row_key="row-1"),
        ]

        app._set_row_status("row-1", "duplicate", STATUS_DONE, "second done")

        self.assertEqual(app.rows[0].status, STATUS_PENDING)
        self.assertEqual(app.rows[1].status, STATUS_DONE)
        self.assertEqual(app.rows[1].message, "second done")
        self.assertEqual(app.table.updated_items, ["item-1"])

    def test_failed_row_uses_failed_tag(self):
        app = OrderBotApp.__new__(OrderBotApp)
        app.table = FakeTable()
        app.table_columns = ["status", "message", "order_id"]
        app.tz = timezone.utc
        app.rows = [RowState(entry=self.make_entry("failed-order"), item_id="item-0", row_key="row-0")]

        app._set_row_status("row-0", "failed-order", STATUS_FAILED, "checkout failed")

        self.assertEqual(app.table.last_tags, (FAILED_ROW_TAG,))

    def test_error_log_messages_use_error_tag(self):
        app = OrderBotApp.__new__(OrderBotApp)
        app.tz = timezone.utc
        app.log = FakeLog()

        app._append_log("AIordertest1: Error checkout failed")

        self.assertEqual(app.log.inserted[0][1], ERROR_LOG_TAG)

    def test_progress_export_data_uses_current_rows(self):
        app = OrderBotApp.__new__(OrderBotApp)
        app.table = FakeTable()
        app.table_columns = ["status", "message", "order_id"]
        app.tz = timezone.utc
        row = RowState(entry=self.make_entry("export-order"), item_id="item-0", row_key="row-0")
        row.status = STATUS_FAILED
        row.message = "bad"
        app.rows = [row]

        headers, rows = app._progress_export_data()

        self.assertEqual(headers, ["状态", "执行信息", "order_id"])
        self.assertEqual(rows[0], [STATUS_FAILED, "bad", "export-order"])


if __name__ == "__main__":
    unittest.main()
