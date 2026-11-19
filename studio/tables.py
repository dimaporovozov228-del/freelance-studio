import csv
from pathlib import Path


def records(headers, rows):
    headers = [str(value or "").strip().lower() for value in headers]
    if not headers or any(not value for value in headers) or len(set(headers)) != len(headers):
        raise ValueError("Headers must be non-empty and unique")
    result = []
    for position, row in enumerate(rows, 2):
        if position > 100001:
            raise ValueError("Input exceeds 100000 rows")
        if all(value is None or str(value).strip() == "" for value in row):
            continue
        if len(row) != len(headers):
            raise ValueError(f"Wrong column count at row {position}")
        result.append(dict(zip(headers, [str(value).strip() if value is not None else "" for value in row])))
    return headers, result


def read_table(path):
    path = Path(path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Input exceeds 10 MiB")
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as source:
            sample = source.read(8192)
            source.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(source, dialect)
            return records(next(reader, []), reader)
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=False)
        try:
            rows = workbook.active.iter_rows(values_only=True)
            return records(next(rows, []), rows)
        finally:
            workbook.close()
    raise ValueError("Only UTF-8 CSV and XLSX supported")


def safe_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_csv(path, rows, headers):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows({key: safe_cell(row.get(key, "")) for key in headers} for row in rows)
