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
            "counterparty_bik": "CASPKZKA",
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
        self.assertEqual("CASPKZKA", self.db.get_tx(tx_id)["counterparty_bik"])

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

    def test_kaspi_table_columns_are_parsed_without_dates_as_amounts(self):
        from app.pdf_parser import parse_text

        text = """
        Наименование и БИК обслуживающего Банка: АО "KASPI BANK" Бик: CASPKZKA
        Дата последнего движения: 17.08.2026 15:49
        ИИН/БИН: 860423399027
        Входящий остаток 623 919,07 KZT
        Номер документа Дата операции Дебет Кредит Наименование получателя
        72 17.08.2026
        15:49:14
        2 000 000 УГД по Есильскому району
        БИН/ИИН 081240013779
        KZ24070105KSN0000000 KKMFKZ2A 911 ИПН с доходов, не облагаемых у источника
        выплаты за июнь 2026г
        64431261 17.08.2026
        15:46:57
        10 398,96 АО "KASPI BANK" БИН/ИИН
        971240001315
        KZ03722S000020267259 841 Оплата за услуги операций по картам Kaspi
        Gold и другим картам за 17/08/2026
        64431226 17.08.2026
        15:45:57
        12 008,57 ТОО Kaspi Pay БИН/ИИН
        200840000951
        KZ86722S000004987101 CASPKZKA 851 Оплата за услуги процессинга Без НДС за
        17/08/2026
        64431198 17.08.2026
        15:45:57
        2 063 980 АО "KASPI BANK" БИН/ИИН
        971240001315
        KZ41722S000020267254 190 Продажи с Kaspi.kz за 17/08/2026
        Отчет сформирован пользователем 17.08.2026 19:47
        """

        bank, _, transactions = parse_text(
            text,
            "Выписка_по_счету_KZ74722S000020043560.pdf",
        )

        self.assertEqual("KASPI", bank)
        self.assertEqual(4, len(transactions))
        self.assertEqual([2000000, 10398.96, 12008.57, 2063980], [item["amount"] for item in transactions])

        tax = transactions[0]
        self.assertEqual("72", tax["document_number"])
        self.assertEqual("KZ74722S000020043560", tax["account"])
        self.assertEqual("УГД по Есильскому району", tax["counterparty_name"])
        self.assertEqual("081240013779", tax["counterparty_bin"])
        self.assertEqual("KZ24070105KSN0000000", tax["counterparty_iban"])
        self.assertEqual("KKMFKZ2A", tax["counterparty_bik"])
        self.assertEqual("911", tax["knp"])
        self.assertEqual("ИПН с доходов, не облагаемых у источника выплаты за июнь 2026г", tax["payment_purpose"])
        self.assertEqual("outgoing", tax["direction"])

        processing = transactions[2]
        self.assertEqual("ТОО Kaspi Pay", processing["counterparty_name"])
        self.assertEqual("CASPKZKA", processing["counterparty_bik"])
        self.assertEqual("851", processing["knp"])

        sales = transactions[3]
        self.assertEqual("incoming", sales["direction"])
        self.assertEqual("Продажи с Kaspi.kz за 17/08/2026", sales["payment_purpose"])


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
