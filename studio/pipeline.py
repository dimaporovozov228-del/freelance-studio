import json
import re
from decimal import Decimal
from pathlib import Path

from .tables import read_table, safe_cell, write_csv

CLEAN_FIELDS = ["order_id", "sku", "customer", "quantity", "unit_price", "product", "total", "match_status"]
ISSUE_FIELDS = ["row", "order_id", "reason"]


def money(value):
    text = str(value).replace("\u00a0", "").replace(" ", "")
    if not re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", text):
        raise ValueError("Price must be non-negative with at most two decimals")
    amount = Decimal(text.replace(",", "."))
    if amount > Decimal("1000000000"):
        raise ValueError("Price exceeds limit")
    return amount.quantize(Decimal("0.01"))


def reconcile(orders_path, catalog_path):
    headers, orders = read_table(orders_path)
    catalog_headers, catalog = read_table(catalog_path)
    if not {"order_id", "sku", "customer", "quantity", "unit_price"} <= set(headers):
        raise ValueError("Orders require order_id, sku, customer, quantity, unit_price")
    if not {"sku", "product"} <= set(catalog_headers):
        raise ValueError("Catalog requires sku, product")
    products = {}
    for row in catalog:
        sku = row["sku"].upper()
        if not sku or not row["product"] or sku in products:
            raise ValueError("Catalog SKU must be unique and product non-empty")
        products[sku] = row["product"]
    clean, issues, seen = [], [], set()
    for position, row in enumerate(orders, 2):
        identifier = row["order_id"]
        try:
            sku = row["sku"].upper()
            if not identifier or not sku or not row["customer"]:
                raise ValueError("Missing order ID, SKU or customer")
            if not re.fullmatch(r"[0-9]+", row["quantity"]):
                raise ValueError("Quantity must be a positive integer")
            quantity = int(row["quantity"])
            if not 1 <= quantity <= 1000000:
                raise ValueError("Quantity outside 1..1000000")
            price = money(row["unit_price"])
            key = (identifier, sku)
            if key in seen:
                raise ValueError("Duplicate order_id + SKU; first valid row retained")
            seen.add(key)
            matched = sku in products
            clean.append(dict(order_id=identifier, sku=sku, customer=row["customer"], quantity=quantity,
                              unit_price=str(price), product=products.get(sku, ""), total=str(price * quantity),
                              match_status="matched" if matched else "unmatched"))
            if not matched:
                issues.append(dict(row=position, order_id=identifier, reason="SKU not found in catalog"))
        except ValueError as error:
            issues.append(dict(row=position, order_id=identifier, reason=str(error)))
    summary = dict(input_rows=len(orders), accepted_rows=len(clean), rejected_rows=len(orders) - len(clean),
                   matched_rows=sum(row["match_status"] == "matched" for row in clean),
                   unmatched_rows=sum(row["match_status"] == "unmatched" for row in clean),
                   total_amount=str(sum((Decimal(row["total"]) for row in clean), Decimal("0.00"))),
                   currency="unspecified; use one currency per input")
    return clean, issues, summary


def export_report(orders, catalog, output):
    clean, issues, summary = reconcile(orders, catalog)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "cleaned.csv", clean, CLEAN_FIELDS)
    write_csv(output / "issues.csv", issues, ISSUE_FIELDS)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows, fields in [("Cleaned", clean, CLEAN_FIELDS), ("Issues", issues, ISSUE_FIELDS),
                               ("Summary", [{"metric": key, "value": str(value)} for key, value in summary.items()], ["metric", "value"])]:
        sheet = workbook.create_sheet(name)
        sheet.append(fields)
        for row in rows:
            sheet.append([safe_cell(row.get(field, "")) for field in fields])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(60, max(14, max(len(str(cell.value or "")) for cell in column) + 2))
    workbook.save(output / "report.xlsx")
    workbook.close()
    return summary
