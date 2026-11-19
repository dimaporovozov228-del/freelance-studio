# Order Desk API

Working local portfolio demo: customers, products, orders, versioned status transitions and audit events. All demo data is synthetic. FastAPI, SQLAlchemy Core, Alembic and SQLite; no paid service required.

From the repository root, Python 3.11+:

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[api,test]"
.venv/Scripts/python.exe -m studio.orderdesk.cli migrate
.venv/Scripts/python.exe -m studio.orderdesk.cli seed
.venv/Scripts/python.exe -m studio.orderdesk.cli serve
```

Skip environment creation if already installed. Linux/macOS: use `python3` and `.venv/bin/python`. Open http://127.0.0.1:8766/docs for interactive requests, `/openapi.json` for schemas, `/health` for status. Stop with Ctrl+C. Parameters: `--db data/orderdesk.sqlite3`, `--port 8766`. Startup applies migrations. Seed inserts one customer and two products only when both tables are empty. Database files are excluded from Git.

In another terminal:

```powershell
.venv/Scripts/python.exe -m studio.orderdesk.demo
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The HTTP demo creates synthetic data, confirms/completes an order totaling 20100 minor units (201.00 RUB), verifies idempotent replay, conflicts and three audit events. Each run adds fresh data. Output: `reports/orderdesk-demo.json`; recorded run: `demo-results/orderdesk-demo.json`.

Endpoints: POST/GET `/customers`, POST/GET `/products`, POST/GET `/orders`, GET `/orders/{id}`, PATCH `/orders/{id}/status`, GET `/orders/{id}/events`. Lists return arrays with `limit` 1..100 (default 20) and `offset` >=0, ordered by ID. Response models and request schemas appear in OpenAPI.

Example POST `/orders`, header `Idempotency-Key: example-order-1`, after seed into an empty database:

```json
{"customer_id":1,"currency":"RUB","items":[{"product_id":1,"quantity":2,"price_minor":10050}]}
```

PATCH the returned order ID at `/orders/{id}/status` with `{"status":"confirmed","version":1}`, then `{"status":"completed","version":2}`. GET `/orders/{id}/events` returns history.

Money is integer minor units; explicit currency RUB/USD/EUR per order, no conversion. Quantity 1..1000000; price 0..1000000000; up to 100 distinct products. Floats, booleans and string numbers are rejected. SKU is normalized to uppercase. Email checks only basic syntax.

States: draft → confirmed → completed; draft/confirmed may be cancelled. Completed/cancelled are terminal. Stale version or invalid transition gives 409. Repeating the same creation key/body returns the original 201 creation response, including its draft status; read current state using GET. Different body gives 409. Keys are database-wide with no expiry; item order is significant. Missing references give 404 and rollback; schema errors give 422; duplicate SKU gives 409.

Orders, items, audit and idempotency are transactional. SQLite enforces foreign keys and numeric constraints. BEGIN IMMEDIATE serializes access, with a 10-second lock wait; exhausted waits can produce a server error. Source: `studio/orderdesk/`; versioned migration: `migrations/versions/0001_initial.py`; tests: `tests/test_orderdesk.py`. Timestamps use UTC. Audit is not tamper-proof against direct database edits. Back up the file with the server stopped before upgrades; restore/downgrade workflows are outside this demo.

Local only, no authentication, delete endpoints, item editing or business dashboard. CLI binds to 127.0.0.1. Public deployment requires access control, HTTPS, rate limits and backup work. Swagger UI assets load from a CDN by default. See [Russian guide](GUIDE.md) for details.
