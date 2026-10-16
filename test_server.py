import tempfile
import unittest
from pathlib import Path

import server
from werkzeug.security import generate_password_hash


class ServerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.preview_mode = server.PREVIEW_MODE
        server.PREVIEW_MODE = False
        self.temporary_directory = tempfile.TemporaryDirectory()
        server.DATABASE = Path(self.temporary_directory.name) / "test.sqlite3"
        server.initialize_database()
        server.app.testing = True
        with server.database_connection() as connection:
            connection.execute(
                "INSERT INTO users (id, name, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', 1)",
                ("admin-id", "Business Admin", "233200000001", generate_password_hash("admin password 123", method="scrypt")),
            )
        self.admin = server.app.test_client()
        self.admin_csrf = self._csrf(self.admin)
        response = self.admin.post(
            "/api/auth/login",
            json={"phone": "0200000001", "password": "admin password 123"},
            headers=self._headers(self.admin_csrf),
        )
        self.assertEqual(response.status_code, 200)
        self.admin_csrf = response.json["csrf_token"]
        self.investor = server.app.test_client()
        self.investor_csrf = self._csrf(self.investor)
        response = self.investor.post(
            "/api/auth/register",
            json={"name": "Investor One", "phone": "0241234567", "password": "investor password 123"},
            headers=self._headers(self.investor_csrf),
        )
        self.assertEqual(response.status_code, 201)
        self.investor_csrf = response.json["csrf_token"]
        self.investor_id = response.json["user"]["id"]

    def tearDown(self):
        server.PREVIEW_MODE = self.preview_mode
        server.app.testing = False
        self.temporary_directory.cleanup()

    @staticmethod
    def _headers(csrf):
        return {"X-CSRF-Token": csrf}

    @staticmethod
    def _csrf(client):
        response = client.get("/api/session")
        return response.json["csrf_token"]

    def _approve_deposit(self, amount="40.50"):
        response = self.investor.post(
            "/api/deposits",
            json={"amount": amount, "reference": "0241234567"},
            headers=self._headers(self.investor_csrf),
        )
        self.assertEqual(response.status_code, 201)
        transaction_id = response.json["id"]
        with server.database_connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT balance_pesewas FROM users WHERE id = ?", (self.investor_id,)
            ).fetchone()[0], 0)
        response = self.admin.post(
            f"/api/admin/requests/{transaction_id}/review",
            json={"action": "approve"},
            headers=self._headers(self.admin_csrf),
        )
        self.assertEqual(response.status_code, 200)

    def test_deposit_is_pending_until_admin_approves(self):
        self._approve_deposit()
        dashboard = self.investor.get("/api/dashboard").json
        self.assertEqual(dashboard["user"]["balance_pesewas"], 4050)
        self.assertEqual(dashboard["transactions"][0]["status"], "approved")

    def test_withdrawal_reserves_balance_and_rejection_refunds(self):
        self._approve_deposit()
        response = self.investor.post(
            "/api/withdrawals",
            json={"amount": "40.51", "provider": "mtn", "phone": "0241234567"},
            headers=self._headers(self.investor_csrf),
        )
        self.assertEqual(response.status_code, 409)
        response = self.investor.post(
            "/api/withdrawals",
            json={"amount": "15.00", "provider": "mtn", "phone": "0241234567"},
            headers=self._headers(self.investor_csrf),
        )
        self.assertEqual(response.status_code, 201)
        dashboard = self.investor.get("/api/dashboard").json
        self.assertEqual(dashboard["user"]["balance_pesewas"], 2550)
        response = self.admin.post(
            f"/api/admin/requests/{response.json['id']}/review",
            json={"action": "reject"},
            headers=self._headers(self.admin_csrf),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.investor.get("/api/dashboard").json["user"]["balance_pesewas"], 4050)

    def test_investor_cannot_call_admin_routes(self):
        response = self.investor.get("/api/admin")
        self.assertEqual(response.status_code, 403)

    def test_health_check_requires_database(self):
        response = self.investor.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_free_preview_blocks_money_actions(self):
        server.PREVIEW_MODE = True
        dashboard = self.investor.get("/api/dashboard")
        self.assertTrue(dashboard.json["preview_mode"])
        self.assertEqual(dashboard.json["user"]["balance_pesewas"], 0)

        requests = [
            ("/api/deposits", {"amount": "20", "reference": "0241234567"}),
            ("/api/withdrawals", {"amount": "1", "provider": "mtn", "phone": "0241234567"}),
            ("/api/investments", {"product_id": "electrical-gadgets"}),
        ]
        for path, body in requests:
            with self.subTest(path=path):
                response = self.investor.post(path, json=body, headers=self._headers(self.investor_csrf))
                self.assertEqual(response.status_code, 503)

        response = self.admin.post(
            f"/api/admin/users/{self.investor_id}/balance",
            json={"action": "credit", "amount": "100", "note": "preview test"},
            headers=self._headers(self.admin_csrf),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.investor.get("/api/dashboard").json["user"]["balance_pesewas"], 0)

    def test_postgres_adapter_translates_placeholders(self):
        class RecordingConnection:
            query = ""

            def execute(self, query, parameters):
                self.query = query
                self.parameters = parameters

        raw = RecordingConnection()
        connection = server.DatabaseConnection(raw, postgres=True)
        connection.execute("SELECT * FROM users WHERE id = ? AND phone = ?", ("id", "phone"))
        self.assertEqual(raw.query, "SELECT * FROM users WHERE id = %s AND phone = %s")
        self.assertEqual(raw.parameters, ("id", "phone"))

    def test_daily_dividends_are_credited_once_per_elapsed_day(self):
        started_at = int(server.time.time()) - 2 * 86400
        with server.database_connection() as connection:
            connection.execute(
                """INSERT INTO investments
                (id, user_id, product_id, product_name, price_pesewas, daily_pesewas, duration_days, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("investment-one", self.investor_id, "electrical-gadgets", "Electrical gadgets", 8000, 500, 30, started_at),
            )

        first = self.investor.get("/api/dashboard").json
        second = self.investor.get("/api/dashboard").json

        self.assertEqual(first["user"]["balance_pesewas"], 1000)
        self.assertEqual(second["user"]["balance_pesewas"], 1000)
        self.assertEqual(len([item for item in second["transactions"] if item["type"] == "dividend"]), 2)


if __name__ == "__main__":
    unittest.main()
