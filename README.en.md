# Freelance Studio · portfolio

[Русский](README.md)

**Sheet Studio** is a working local CSV/XLSX sales cleanup and reconciliation demonstrator.
All example companies and jobs are synthetic. This is a portfolio demonstration, with no client testimonials or commercial delivery claims.

## Install and run

Python 3.11+; run from the repository root:

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -m studio.cli examples/orders.csv examples/catalog.csv --output reports
.venv/Scripts/python.exe -m studio.web
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m studio.scout examples/jobs.json --output reports/jobs-ranked.json
```

The environment in this delivered workspace is already installed; start with the CLI command.
On Linux/macOS use `python3 -m venv .venv` and `.venv/bin/python` instead.
Open http://127.0.0.1:8765, upload orders and catalog, download the ZIP. Ctrl+C stops the server.
Installed console entry points: `sheet-studio` and `job-scout`.

## Contract and behavior

Orders: `order_id,sku,customer,quantity,unit_price`; catalog: `sku,product`.
UTF-8 CSV supports comma, semicolon and tab delimiters. XLSX reads the active sheet.
Whitespace is trimmed, headers lowercased and SKUs uppercased. Quantity must be an integer in 1..1000000; price must be non-negative, at most 1 billion and have at most two decimals. Both `100.50` and `1 200,50` work.
The first valid `order_id + sku` wins; later duplicates are rejected. Catalog SKU duplicates reject the entire input.
Matching is exact after normalization. Unknown SKUs are retained and flagged, and their amounts remain in the total.
Money uses Decimal; use a single currency per input. Workbook numbers are exported as text to preserve exact decimal representation.

Outputs: `cleaned.csv`, `issues.csv`, `summary.json`, and `report.xlsx` containing Cleaned, Issues and Summary sheets.
The sample yields **7 input / 4 accepted / 3 rejected / 1 unmatched / 3851.00 total**. There are four issues including the unmatched row.
Actual generated artifacts are in `demo-results/`.

## Repository

`studio/` holds the engine, CLI, local web UI and offline job evaluator; `examples/` synthetic inputs; `tests/` pipeline, HTTP and scoring tests; `projects/` two planned project specifications; `docs/` studio workflow and source policy. CI is supplied in `.github/workflows/`.

## Limits

Local single-user demo, bound to loopback; not a public deployment. No paid API, cloud service or LLM is needed. Uploads are temporary and removed after ZIP generation.
CLI inputs: 10 MiB each, 100000 rows; web JSON request: 2 MiB including base64 (~1.5 MiB combined files). Compressed workbooks may expand considerably; use trusted files.
XLSX formulas are not evaluated and fail numeric validation. Exported formula prefixes are escaped. No `.xls`, macros, fuzzy matching, mixed currency or large-scale processing.
Blank rows are skipped; issue row numbers refer to nonblank record positions plus the header, not necessarily physical file lines.
Use a separate output directory; generated filenames overwrite previous reports.
The job evaluator accepts only authorized manual exports, provides transparent heuristic scores, never accesses remote pages and never submits proposals. See `docs/job-scout.md`.

Next: record a demo, publish to an owner-selected repository, implement the API/database design, then the human-approved workflow. Neither additional project is implemented yet.
