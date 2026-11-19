# Inbox Triage

Working local portfolio demo: import authorized JSON/CSV messages, classify into billing/support/sales/unknown, generate template drafts, edit and manually approve/reject, inspect audit events and export approved drafts. All examples are synthetic. The active engine uses rules, not a connected LLM; no paid API or outbound delivery exists.

From the repository root with Python 3.11+:

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[api,test]"
.venv/Scripts/python.exe -m studio.triage.cli serve
```

Skip environment creation if it already exists. Linux/macOS: use `python3` and `.venv/bin/python`. Open http://127.0.0.1:8767. Stop with Ctrl+C. CLI options: `--port`, `--db` (default `data/inbox.sqlite3`, separate from Order Desk). First startup initializes schema version 1; incompatible/foreign databases are rejected. Future schema changes require a new migration.

Click “Загрузить 30 примеров” to load thirty examples, select an item, edit its draft, enter a reviewer name and approve (“Одобрить черновик”) or reject (“Отклонить”). Unknown or low-confidence messages require explicitly selecting and confirming a category first. Category changes do not regenerate the draft. Approved/rejected messages are terminal. Download approved CSV using “Скачать одобренные CSV”; pending/rejected items never appear in it. Repeating the seed skips duplicates.

Input fields: source, external_id, body, received_at (ISO 8601 with timezone); no extras. JSON is an array; UTF-8 CSV uses those headers in that order. Example: `examples/triage-import.csv`. Up to 512 KiB, 1000 messages and 10000 characters per body. The import requires a permission note; this records the user's assertion, not a legal verification. Conflicting content/timestamps for an existing source/ID roll back the whole batch. Database stores hashed original identifiers and masked display values.

```powershell
.venv/Scripts/python.exe -m studio.triage.cli seed
.venv/Scripts/python.exe -m studio.triage.cli import --input examples/triage-import.csv --permission-note "Own synthetic data"
.venv/Scripts/python.exe -m studio.triage.cli export --output reports/approved-drafts.csv
.venv/Scripts/python.exe -m studio.triage.cli evaluate --input examples/triage-evaluation.json --output reports/triage-evaluation.json
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Measured rule accuracy: 16/20 (80%) on a small, separately authored synthetic labelled set; four missed categories became unknown. Full confusion matrix: `demo-results/triage-evaluation.json`. This is not independent validation or a client performance guarantee. Rule confidence values are not calibrated probabilities. Multi-category, unrecognized and flagged instruction-like messages need manual review. Every import creates a draft, never an approved item.

`ClassifierAdapter` is a Python-only optional model contract, not an active provider integration. It receives masked text capped at 4000 characters and must return strict category/confidence/priority/rationale JSON. At most two attempts, three seconds each, two bounded daemon worker slots; errors/timeouts/extra fields fall back to rules. Timed-out threads retain a slot until they finish. Real providers must implement network timeouts, token/cost limits and require separate data-sharing approval. Ambiguous/flagged input is not passed to the adapter. Tests use stubs.

Local-only, no authenticated identity, roles, mailbox/CRM connection or sending endpoint. Reviewer names are audit labels, not verified identities. A per-process page token protects mutations; refresh after restart. Anyone with access to the local page can perform reviews. SQLite contains masked messages, hashes and draft history; masking is heuristic and may miss names, addresses or secrets, so use pre-anonymized input. Audit is not tamper-proof. CSV formula prefixes are escaped; UI uses textContent for imported content. Prompt injection detection is illustrative, not comprehensive protection. Back up with the server stopped. See [Russian guide](GUIDE.md) for full details.
