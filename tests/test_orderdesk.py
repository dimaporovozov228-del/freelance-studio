import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from studio.orderdesk.api import create_app
from studio.orderdesk.db import migrate
from studio.orderdesk.cli import seed


class OrderDeskTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "orders.sqlite3"
        self.app = create_app(self.path)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.customer = self.client.post("/customers", json={"name": "Synthetic Retail", "email": "demo@example.com"}).json()
        self.product = self.client.post("/products", json={"sku": "demo-01", "name": "Demo cable"}).json()
        self.body = {"customer_id": self.customer["id"], "currency": "RUB", "items": [
            {"product_id": self.product["id"], "quantity": 2, "price_minor": 10050}]}

    def create_order(self, key="demo-key", body=None):
        return self.client.post("/orders", json=body or self.body, headers={"Idempotency-Key": key})

    def test_health_migration_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "schema_revision": "0001"})
        migrate(self.app.state.engine)
        self.assertEqual(self.client.get("/customers").json()[0]["name"], "Synthetic Retail")
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertIn("/orders/{order_id}/events", self.client.get("/openapi.json").json()["paths"])

    def test_order_lifecycle_and_audit(self):
        response = self.create_order()
        self.assertEqual(response.status_code, 201, response.text)
        order = response.json()
        self.assertEqual((order["status"], order["version"], order["total_minor"]), ("draft", 1, 20100))
        endpoint = f"/orders/{order['id']}"
        self.assertEqual(self.client.get(endpoint).json(), order)
        for version, status in [(1, "confirmed"), (2, "completed")]:
            response = self.client.patch(endpoint + "/status", json={"status": status, "version": version})
            self.assertEqual(response.status_code, 200, response.text)
        events = self.client.get(endpoint + "/events").json()
        self.assertEqual([event["action"] for event in events], ["created", "draft->confirmed", "confirmed->completed"])
        self.assertEqual(self.client.patch(endpoint + "/status", json={"status": "cancelled", "version": 3}).status_code, 409)
        self.assertEqual(len(self.client.get(endpoint + "/events").json()), 3)

    def test_idempotency_preserves_original_response(self):
        original = self.create_order().json()
        self.client.patch(f"/orders/{original['id']}/status", json={"status": "confirmed", "version": 1})
        self.assertEqual(self.create_order().json(), original)
        changed = {**self.body, "currency": "USD"}
        self.assertEqual(self.create_order(body=changed).status_code, 409)
        self.assertEqual(len(self.client.get("/orders").json()), 1)
        self.assertEqual(self.client.get(f"/orders/{original['id']}").json()["status"], "confirmed")

    def test_missing_product_rolls_back_entire_order(self):
        body = {**self.body, "items": self.body["items"] + [{"product_id": 999, "quantity": 1, "price_minor": 100}]}
        self.assertEqual(self.create_order(body=body).status_code, 404)
        with self.app.state.engine.begin() as connection:
            for table in ("orders", "order_items", "audit_events", "idempotency"):
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one(), 0)
        self.assertEqual(self.create_order().status_code, 201)

    def test_validation_and_missing_references(self):
        for quantity in (0, -1, 1.5, True, "2", 1000001):
            body = {**self.body, "items": [{**self.body["items"][0], "quantity": quantity}]}
            self.assertEqual(self.create_order(body=body).status_code, 422)
        for price in (-1, 1.1, True, "100", 1000000001):
            body = {**self.body, "items": [{**self.body["items"][0], "price_minor": price}]}
            self.assertEqual(self.create_order(body=body).status_code, 422)
        self.assertEqual(self.create_order(body={**self.body, "items": []}).status_code, 422)
        self.assertEqual(self.create_order(body={**self.body, "items": self.body["items"] * 2}).status_code, 422)
        self.assertEqual(self.create_order(body={**self.body, "currency": "XXX"}).status_code, 422)
        self.assertEqual(self.create_order(body={**self.body, "customer_id": 999}).status_code, 404)
        self.assertEqual(self.client.post("/orders", json=self.body).status_code, 422)
        self.assertEqual(self.client.get("/orders/999").status_code, 404)
        self.assertEqual(self.client.get("/orders/999/events").status_code, 404)
        self.assertEqual(self.client.get("/orders/-1").status_code, 422)
        self.assertEqual(self.client.post("/customers", json={"name": "  ", "email": "bad"}).status_code, 422)

    def test_sku_uniqueness_and_pagination(self):
        duplicate = self.client.post("/products", json={"sku": "DEMO-01", "name": "Another name"})
        self.assertEqual(duplicate.status_code, 409)
        self.create_order("one")
        self.create_order("two")
        first = self.client.get("/orders?limit=1&offset=0").json()
        second = self.client.get("/orders?limit=1&offset=1").json()
        self.assertEqual((len(first), len(second)), (1, 1))
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertEqual(self.client.get("/orders?offset=2").json(), [])
        for query in ("limit=0", "limit=101", "offset=-1"):
            self.assertEqual(self.client.get("/orders?" + query).status_code, 422)

    def test_status_conflicts_and_cancellation(self):
        order = self.create_order().json()
        endpoint = f"/orders/{order['id']}/status"
        self.assertEqual(self.client.patch(endpoint, json={"status": "completed", "version": 1}).status_code, 409)
        self.assertEqual(self.client.patch(endpoint, json={"status": "cancelled", "version": 1}).status_code, 200)
        self.assertEqual(self.client.patch(endpoint, json={"status": "confirmed", "version": 1}).status_code, 409)
        self.assertEqual(self.client.patch(endpoint, json={"status": "confirmed", "version": 2}).status_code, 409)

    def test_parallel_idempotency_and_optimistic_update(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda unused: self.create_order("parallel"), range(2)))
        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(responses[0].json(), responses[1].json())
        order_id = responses[0].json()["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda status: self.client.patch(f"/orders/{order_id}/status", json={"status": status, "version": 1}),
                                      ["confirmed", "cancelled"]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(len(self.client.get(f"/orders/{order_id}/events").json()), 2)

    def test_database_constraints_and_persistence(self):
        with self.assertRaises(IntegrityError), self.app.state.engine.begin() as connection:
            connection.execute(text("INSERT INTO order_items(order_id,product_id,quantity,price_minor) VALUES (999,999,1,1)"))
        order = self.create_order().json()
        with self.assertRaises(IntegrityError), self.app.state.engine.begin() as connection:
            connection.execute(text("UPDATE order_items SET quantity=-1 WHERE order_id=:id"), {"id": order["id"]})
        with TestClient(create_app(self.path)) as reopened:
            self.assertEqual(reopened.get(f"/orders/{order['id']}").json(), order)

    def test_seed_is_repeatable(self):
        target = Path(self.folder.name) / "seed.sqlite3"
        seed(target)
        seed(target)
        with TestClient(create_app(target)) as seeded:
            self.assertEqual(len(seeded.get("/customers").json()), 1)
            self.assertEqual(len(seeded.get("/products").json()), 2)


if __name__ == "__main__":
    unittest.main()
