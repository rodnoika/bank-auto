import os
import unittest

from fastapi.testclient import TestClient


class APIFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), "tests", "_api.sqlite3")
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        os.environ["APP_API_KEY"] = "test-key"
        os.environ["DATABASE_PATH"] = cls.db_path

        from app import db

        db.DB = cls.db_path
        from app.main import app

        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)

    def test_health_and_home(self):
        self.assertEqual(
            {
                "ok": True,
                "ai_enabled": False,
                "ai_model": "gpt-4o",
                "database": "sqlite",
                "max_pdf_mb": 15,
            },
            self.client.get("/health").json(),
        )
        root = self.client.get("/")
        self.assertEqual(200, root.status_code)
        self.assertEqual("http://localhost:3000", root.json()["ui"])

    def test_vercel_pdf_limit_is_capped(self):
        from app.main import max_pdf_mb

        original_vercel = os.environ.get("VERCEL")
        original_limit = os.environ.get("MAX_PDF_MB")
        try:
            os.environ["VERCEL"] = "1"
            os.environ["MAX_PDF_MB"] = "15"
            self.assertEqual(4, max_pdf_mb())
        finally:
            if original_vercel is None:
                os.environ.pop("VERCEL", None)
            else:
                os.environ["VERCEL"] = original_vercel
            if original_limit is None:
                os.environ.pop("MAX_PDF_MB", None)
            else:
                os.environ["MAX_PDF_MB"] = original_limit

    def test_confirmation_gate_for_1c_export(self):
        payload = {
            "transactions": [{
                "bank": "KASPI",
                "operation_date": "2026-08-11",
                "amount": 5000,
                "direction": "incoming",
                "currency": "KZT",
                "payment_purpose": "Оплата",
            }]
        }
        unauthorized = self.client.post("/api/v1/transactions/import-json", json=payload)
        self.assertEqual(401, unauthorized.status_code)

        headers = {"X-API-Key": "test-key"}
        imported = self.client.post("/api/v1/transactions/import-json", json=payload, headers=headers)
        self.assertEqual(1, imported.json()["created"])
        tx_id = self.client.get("/api/v1/transactions?status=review").json()["items"][0]["id"]
        self.assertEqual([], self.client.get("/api/v1/transactions/export", headers=headers).json()["items"])

        approved = self.client.post(f"/api/v1/transactions/{tx_id}/approve", headers=headers)
        self.assertEqual(200, approved.status_code)
        ready = self.client.get("/api/v1/transactions/export", headers=headers).json()["items"]
        self.assertEqual([tx_id], [item["id"] for item in ready])

        marked = self.client.post(f"/api/v1/transactions/{tx_id}/mark-exported", headers=headers)
        self.assertEqual(200, marked.status_code)


if __name__ == "__main__":
    unittest.main()
