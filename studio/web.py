import argparse
import base64
import io
import json
import secrets
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .pipeline import export_report


def page(token):
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Sheet Studio</title>
<style>body{font:17px system-ui;max-width:850px;margin:50px auto;padding:20px;background:#f3f6fa}button{padding:15px;background:#145bcc;color:white;border:0}</style>
<h1>Sheet Studio</h1><p>Локальная сверка продаж · синтетический демопроект</p>
<p>Загрузите CSV или XLSX. Результат: очищенные строки, ошибки, сводка и Excel в ZIP.</p>
<p>Заказы: order_id, sku, customer, quantity, unit_price. Каталог: sku, product.</p>
<label>Заказы <input type="file" id="orders" accept=".csv,.xlsx"></label><br><br>
<label>Каталог <input type="file" id="catalog" accept=".csv,.xlsx"></label><br><br>
<button id="run">Сформировать отчёт</button><p id="status" role="status"></p>
<script>
const token=TOKEN;
async function encode(file){const bytes=new Uint8Array(await file.arrayBuffer());let result='';for(const byte of bytes) result+=String.fromCharCode(byte);return btoa(result);}
document.getElementById('run').onclick=async()=>{const status=document.getElementById('status');try{
const orders=document.getElementById('orders').files[0],catalog=document.getElementById('catalog').files[0];
if(!orders||!catalog)throw Error('Выберите оба файла');status.textContent='Обработка…';
const response=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json','X-Token':token},body:JSON.stringify({orders:await encode(orders),orders_name:orders.name,catalog:await encode(catalog),catalog_name:catalog.name})});
if(!response.ok)throw Error(await response.text());const url=URL.createObjectURL(await response.blob());
const link=document.createElement('a');link.href=url;link.download='sheet-studio-report.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);status.textContent='Отчёт готов';
}catch(error){status.textContent=error.message;}};
</script></html>'''.replace("TOKEN", json.dumps(token))


class Handler(BaseHTTPRequestHandler):
    token = secrets.token_urlsafe(32)

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def respond(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            self.respond(404, b"Not found", "text/plain")
            return
        self.respond(200, page(self.token).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/run" or self.headers.get("X-Token") != self.token:
            self.respond(403, b"Forbidden", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("Request must be between 1 byte and 2 MiB")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                paths = []
                for name in ("orders", "catalog"):
                    extension = Path(payload[name + "_name"]).suffix.lower()
                    if extension not in (".csv", ".xlsx"):
                        raise ValueError("Only CSV and XLSX supported")
                    target = root / (name + extension)
                    target.write_bytes(base64.b64decode(payload[name], validate=True))
                    paths.append(target)
                export_report(*paths, root / "report")
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted((root / "report").iterdir()):
                        archive.write(item, item.name)
                self.respond(200, buffer.getvalue(), "application/zip")
        except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as error:
            self.respond(400, str(error).encode(), "text/plain; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="Local-only Sheet Studio UI")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Open http://127.0.0.1:{args.port}; Ctrl+C to stop", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
