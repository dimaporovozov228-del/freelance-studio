import argparse
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def run_demo(base_url):
    steps = []

    def request(method, path, expected, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        payload = json.dumps(body).encode() if body is not None else None
        message = urllib.request.Request(base_url.rstrip("/") + path, data=payload, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(message, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.code
            data = json.loads(response.read())
        steps.append({"method": method, "path": path, "status": status, "response": data})
        if status != expected:
            raise RuntimeError(f"{method} {path}: expected {expected}, got {status}: {data}")
        return data

    suffix = uuid.uuid4().hex[:12]
    request("GET", "/health", 200)
    customer = request("POST", "/customers", 201, {"name": "Synthetic Demo Retail", "email": "demo@example.com"})
    product = request("POST", "/products", 201, {"sku": "DEMO-" + suffix, "name": "Synthetic cable"})
    body = {"customer_id": customer["id"], "currency": "RUB", "items": [
        {"product_id": product["id"], "quantity": 2, "price_minor": 10050}]}
    order = request("POST", "/orders", 201, body, suffix)
    endpoint = f"/orders/{order['id']}"
    request("PATCH", endpoint + "/status", 200, {"status": "confirmed", "version": 1})
    replay = request("POST", "/orders", 201, body, suffix)
    if replay != order:
        raise RuntimeError("Idempotent replay differs from original response")
    request("POST", "/orders", 409, {**body, "currency": "USD"}, suffix)
    request("PATCH", endpoint + "/status", 409, {"status": "completed", "version": 1})
    completed = request("PATCH", endpoint + "/status", 200, {"status": "completed", "version": 2})
    events = request("GET", endpoint + "/events", 200)
    if completed["total_minor"] != 20100 or len(events) != 3:
        raise RuntimeError("Unexpected total or audit count")
    return {"synthetic": True, "result": "passed", "order_id": order["id"], "currency": "RUB",
            "total_minor": completed["total_minor"], "final_status": completed["status"],
            "audit_events": len(events), "steps": steps}


def main():
    parser = argparse.ArgumentParser(description="Create a synthetic order and verify the running local API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--output", default="reports/orderdesk-demo.json")
    args = parser.parse_args()
    try:
        result = run_demo(args.base_url)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "steps"}, indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"Demo failed: {error}\n")


if __name__ == "__main__":
    main()
