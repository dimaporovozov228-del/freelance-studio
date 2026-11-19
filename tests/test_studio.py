import base64
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from openpyxl import Workbook, load_workbook
from studio.pipeline import export_report, money, reconcile
from studio.scout import evaluate
from studio.tables import read_table, safe_cell
from studio.web import Handler

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def test_demo_totals_and_rejections(self):
        clean, issues, summary = reconcile(ROOT / "examples/orders.csv", ROOT / "examples/catalog.csv")
        self.assertEqual((summary["input_rows"], summary["accepted_rows"], summary["rejected_rows"]), (7, 4, 3))
        self.assertEqual(summary["total_amount"], "3851.00")
        self.assertEqual(summary["unmatched_rows"], 1)
        self.assertEqual(len(issues), 4)
        self.assertEqual(clean[0]["sku"], "ABC-01")

    def test_invalid_money(self):
        for value in ("NaN", "Infinity", "-2", "1.234", "1e3", "1000000001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)
        self.assertEqual(str(money("1 200,50")), "1200.50")

    def test_export_workbook_and_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            export_report(ROOT / "examples/orders.csv", ROOT / "examples/catalog.csv", folder)
            workbook = load_workbook(Path(folder) / "report.xlsx")
            self.assertEqual(workbook.sheetnames, ["Cleaned", "Issues", "Summary"])
            self.assertEqual(workbook["Cleaned"].max_row, 5)
            workbook.close()
            self.assertEqual(len(read_table(Path(folder) / "cleaned.csv")[1]), 4)

    def test_xlsx_input(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "orders.xlsx"
            workbook = Workbook()
            workbook.active.append(["order_id", "sku", "customer", "quantity", "unit_price"])
            workbook.active.append(["X", "abc-01", "Demo", 2, 0.1])
            workbook.save(target)
            workbook.close()
            self.assertEqual(reconcile(target, ROOT / "examples/catalog.csv")[2]["total_amount"], "0.20")

    def test_bad_schema_duplicate_catalog_and_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "bad.csv"
            for content in ("", "sku,sku\na,b", "sku,product\nABC-01,A\nabc-01,B", "sku,product\na"):
                target.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    reconcile(ROOT / "examples/orders.csv", target)

    def test_formula_escaping(self):
        for text in ("=1+1", " +SUM(A1)", "@cmd", "-1+2"):
            self.assertTrue(safe_cell(text).startswith("'"))
        self.assertEqual(safe_cell("Normal"), "Normal")


class ScoutTests(unittest.TestCase):
    def test_ranking_and_deduplication(self):
        jobs = json.loads((ROOT / "examples/jobs.json").read_text())
        result = evaluate(jobs + jobs)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["id"], "demo-1")
        self.assertEqual(result[-1]["score"], 0)
        self.assertTrue(all(not row["outreach_sent"] for row in result))

    def test_source_gate(self):
        for job in ({}, {"source_type": "scraper"}, {"source_type": "manual_export", "permission_note": ""}):
            with self.assertRaises(ValueError):
                evaluate([job])


class WebTests(unittest.TestCase):
    def test_idle_browser_connection_does_not_block_page(self):
        accepted = threading.Event()

        class ObservedHandler(Handler):
            def setup(self):
                super().setup()
                accepted.set()

        with ThreadingHTTPServer(("127.0.0.1", 0), ObservedHandler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            idle = socket.create_connection(server.server_address, timeout=2)
            try:
                self.assertTrue(accepted.wait(2))
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}", timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Sheet Studio", response.read())
            finally:
                idle.close()
                server.shutdown()
                worker.join(timeout=3)

    def test_http_upload_zip_and_auth(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(url) as response:
                    self.assertIn(b"Sheet Studio", response.read())
                data = {}
                for name in ("orders", "catalog"):
                    data[name] = base64.b64encode((ROOT / f"examples/{name}.csv").read_bytes()).decode()
                    data[name + "_name"] = name + ".csv"
                payload = json.dumps(data).encode()
                request = urllib.request.Request(url + "/run", data=payload, headers={"X-Token": Handler.token})
                with urllib.request.urlopen(request) as response:
                    with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                        self.assertEqual(json.loads(archive.read("summary.json"))["total_amount"], "3851.00")
                        self.assertIn("report.xlsx", archive.namelist())
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(urllib.request.Request(url + "/run", data=payload))
                self.assertEqual(failure.exception.code, 403)
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
