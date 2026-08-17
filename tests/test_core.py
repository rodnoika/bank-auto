import os
import unittest


class CoreFlowTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(os.getcwd(), "tests", "_test.sqlite3")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.environ["DATABASE_PATH"] = self.db_path
        from app import db

        db.DB = os.environ["DATABASE_PATH"]
        db.init_db()
        self.db = db

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def sample(self, **changes):
        item = {
            "bank": "KASPI",
            "account": "KZ000000000000000000",
            "operation_date": "2026-08-10",
            "amount": 125000,
            "direction": "incoming",
            "currency": "KZT",
            "counterparty_bin": "123456789012",
            "payment_purpose": "Оплата по счету 458",
            "source_type": "pdf",
            "source_name": "statement.pdf",
        }
        item.update(changes)
        return item

    def test_review_before_export_and_deduplication(self):
        tx_id, duplicate = self.db.insert_tx(self.sample())
        self.assertFalse(duplicate)
        self.assertEqual("review", self.db.get_tx(tx_id)["status"])
        self.assertEqual([], self.db.list_tx(status="ready"))

        same_id, duplicate = self.db.insert_tx(self.sample())
        self.assertTrue(duplicate)
        self.assertEqual(tx_id, same_id)

        self.assertEqual(1, self.db.approve_many([tx_id]))
        self.assertEqual([tx_id], [item["id"] for item in self.db.list_tx(status="ready")])
        self.assertTrue(self.db.mark_exported(tx_id))
        self.assertEqual("exported", self.db.get_tx(tx_id)["status"])

    def test_editing_keeps_import_fingerprint(self):
        tx_id, _ = self.db.insert_tx(self.sample())
        before = self.db.get_tx(tx_id)["fingerprint"]
        updated = self.db.update_tx(tx_id, {"payment_purpose": "Исправленный текст", "status": "exported"})
        self.assertEqual("Исправленный текст", updated["payment_purpose"])
        self.assertEqual("review", updated["status"])
        self.assertEqual(before, updated["fingerprint"])


class ParserTests(unittest.TestCase):
    def test_bank_detection(self):
        from app.pdf_parser import detect_bank

        self.assertEqual("KASPI", detect_bank("Kaspi Business"))
        self.assertEqual("BCC", detect_bank("Bank CenterCredit"))
        self.assertEqual("FREEDOM", detect_bank("Freedom Bank Kazakhstan"))
        self.assertEqual("UNKNOWN", detect_bank("без названия банка"))


class AITextTests(unittest.TestCase):
    def test_extracts_structured_output_text(self):
        from app.ai_text import _output_text

        payload = {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": '{"summary":"ok"}'}],
            }]
        }
        self.assertEqual('{"summary":"ok"}', _output_text(payload))


class DatabaseDialectTests(unittest.TestCase):
    def test_postgres_placeholders_are_adapted(self):
        from app import db

        class FakeConnection:
            def __init__(self):
                self.call = None

            def execute(self, sql, params):
                self.call = (sql, params)
                return self

        original = db.DATABASE_URL
        fake = FakeConnection()
        try:
            db.DATABASE_URL = "postgresql://example.test/bankhub"
            db.execute(fake, "SELECT * FROM transactions WHERE id=? AND status=?", (1, "ready"))
        finally:
            db.DATABASE_URL = original

        self.assertEqual(
            ("SELECT * FROM transactions WHERE id=%s AND status=%s", (1, "ready")),
            fake.call,
        )


if __name__ == "__main__":
    unittest.main()
