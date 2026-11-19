import csv
import hashlib
import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from studio.tables import safe_cell
from .rules import CATEGORIES, TEMPLATES, classify_with_adapter, redact


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_messages(content, suffix):
    if len(content.encode("utf-8")) > 512 * 1024:
        raise ValueError("Import exceeds 512 KiB")
    if suffix.lower() == ".json":
        rows = json.loads(content.lstrip("\ufeff"))
    elif suffix.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if reader.fieldnames != ["source", "external_id", "body", "received_at"]:
            raise ValueError("CSV columns must be source,external_id,body,received_at in that order")
        rows = list(reader)
    else:
        raise ValueError("Use JSON or UTF-8 CSV")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Expected 1..1000 messages")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"source", "external_id", "body", "received_at"}:
            raise ValueError("Each message requires source, external_id, body, received_at only")
        for key, limit in [("source", 80), ("external_id", 80), ("body", 10000), ("received_at", 50)]:
            if not isinstance(row[key], str) or not 1 <= len(row[key].strip()) <= limit:
                raise ValueError(f"Invalid {key}")
        timestamp = datetime.fromisoformat(row["received_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("received_at must include timezone")
        normalized.append({**{key: value.strip() for key, value in row.items()},
                           "received_at": timestamp.astimezone(timezone.utc).isoformat()})
    return normalized


class Inbox:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported inbox database version")
            if version == 0:
                if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Use a separate empty database for Inbox Triage")
                connection.execute("""CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
                    source_key TEXT NOT NULL, external_key TEXT NOT NULL,
                    body TEXT NOT NULL, body_hash TEXT NOT NULL, received_at TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('billing','support','sales','unknown')),
                    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1), priority TEXT NOT NULL,
                    rationale TEXT NOT NULL, engine TEXT NOT NULL, draft TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('draft','approved','rejected')),
                    reviewer TEXT NOT NULL DEFAULT '', human_category TEXT,
                    version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1), UNIQUE(source_key,external_key))""")
                connection.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY,
                    message_id INTEGER NOT NULL REFERENCES messages(id), action TEXT NOT NULL,
                    details TEXT NOT NULL, timestamp TEXT NOT NULL)""")
                connection.execute("CREATE INDEX events_message ON events(message_id,id)")
                connection.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def event(self, connection, identifier, action, details):
        connection.execute("INSERT INTO events(message_id,action,details,timestamp) VALUES (?,?,?,?)",
                           (identifier, action, json.dumps(details, ensure_ascii=False), now()))

    def import_messages(self, content, suffix, permission_note, adapter=None):
        if not isinstance(permission_note, str) or not 1 <= len(permission_note.strip()) <= 300:
            raise ValueError("Provide permission_note for authorized input")
        messages = parse_messages(content, suffix)
        inserted, skipped = 0, 0
        with self.connect() as connection:
            for message in messages:
                digest = hashlib.sha256(message["body"].encode()).hexdigest()
                source_key = hashlib.sha256(message["source"].encode()).hexdigest()
                external_key = hashlib.sha256(message["external_id"].encode()).hexdigest()
                previous = connection.execute("SELECT body_hash,received_at FROM messages WHERE source_key=? AND external_key=?",
                                              (source_key, external_key)).fetchone()
                if previous:
                    if previous["body_hash"] != digest or previous["received_at"] != message["received_at"]:
                        raise Conflict("Existing source/external_id has different content; batch rolled back")
                    skipped += 1
                    continue
                body = redact(message["body"])
                decision = classify_with_adapter(body, adapter)
                cursor = connection.execute("""INSERT INTO messages
                    (source,external_id,source_key,external_key,body,body_hash,received_at,category,confidence,priority,rationale,engine,draft,state)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'draft')""",
                    (redact(message["source"]), redact(message["external_id"]), source_key, external_key, body, digest, message["received_at"],
                     decision["category"], decision["confidence"], decision["priority"], decision["rationale"],
                     decision["engine"], TEMPLATES[decision["category"]]))
                identifier = cursor.lastrowid
                self.event(connection, identifier, "imported", {"permission_note": redact(permission_note)})
                self.event(connection, identifier, "classified", decision)
                self.event(connection, identifier, "draft_created", {"template": decision["category"]})
                inserted += 1
        return {"inserted": inserted, "skipped": skipped, "outreach_sent": False}

    def view(self, row):
        result = dict(row)
        result.pop("body_hash", None)
        result.pop("source_key", None)
        result.pop("external_key", None)
        result["needs_category_review"] = result["human_category"] is None and (result["category"] == "unknown" or result["confidence"] < 0.8)
        return result

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM messages WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFound("Message not found")
            return self.view(row)

    def listing(self, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            counts = {state: 0 for state in ("draft", "approved", "rejected")}
            counts.update({row["state"]: row["count"] for row in connection.execute("SELECT state,count(*) AS count FROM messages GROUP BY state")})
            return {"items": [self.view(row) for row in rows], "counts": counts, "total": sum(counts.values())}

    def review(self, identifier, version, reviewer, action, draft, category=None):
        if type(version) is not int or version < 1 or action not in ("save", "approve", "reject"):
            raise ValueError("Invalid action or version")
        if not isinstance(reviewer, str) or not 2 <= len(reviewer.strip()) <= 80:
            raise ValueError("Reviewer name must contain 2..80 characters")
        if not isinstance(draft, str) or not 1 <= len(draft.strip()) <= 5000:
            raise ValueError("Draft must contain 1..5000 characters")
        if category is not None and category not in CATEGORIES[:-1]:
            raise ValueError("Choose billing, support or sales for manual classification")
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM messages WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise NotFound("Message not found")
            if row["version"] != version or row["state"] != "draft":
                raise Conflict("Stale version or message already reviewed; refresh the queue")
            human_category = category or row["human_category"]
            if action == "approve" and not human_category and (row["category"] == "unknown" or row["confidence"] < 0.8):
                raise Conflict("Explicit manual classification required before approval")
            state = {"save": "draft", "approve": "approved", "reject": "rejected"}[action]
            clean_draft = redact(draft.strip())
            clean_reviewer = redact(reviewer.strip())
            connection.execute("UPDATE messages SET state=?,draft=?,reviewer=?,human_category=?,version=version+1 WHERE id=? AND version=?",
                               (state, clean_draft, clean_reviewer, human_category, identifier, version))
            self.event(connection, identifier, action, {"reviewer": clean_reviewer, "human_category": human_category,
                                                       "previous_draft": row["draft"], "draft": clean_draft, "version": version + 1})
        return self.get(identifier)

    def history(self, identifier):
        self.get(identifier)
        with self.connect() as connection:
            return [{**dict(row), "details": json.loads(row["details"])} for row in connection.execute(
                "SELECT * FROM events WHERE message_id=? ORDER BY id", (identifier,))]

    def export(self):
        fields = ["id", "source", "external_id", "category", "priority", "draft", "reviewer", "state"]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        with self.connect() as connection:
            for row in connection.execute("SELECT * FROM messages WHERE state='approved' ORDER BY id"):
                record = dict(row)
                record["category"] = record["human_category"] or record["category"]
                writer.writerow({key: safe_cell(record[key]) for key in fields})
        return buffer.getvalue()
