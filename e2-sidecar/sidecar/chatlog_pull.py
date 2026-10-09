#!/usr/bin/env python3
"""chatlog_pull.py — pull sanitized Field Assistant turns from R2 into SQLite.

The chat worker (airlens-web, workers/assistant/src/persist.ts) sanitizes each
turn at the edge and drops it into the R2 bucket `airlens-chatlog` as one JSON
object. This script is the other end: it lists that bucket, inserts what it
finds, and deletes only what it has confirmed is stored.

The transfer is PULL-ONLY on purpose. This VM accepts no inbound connections
and neither does the worker's origin — nothing here opens a port, and the
credential only reaches one bucket.

Stdlib only (SigV4 lives in hmac/hashlib, SQLite is built in). On a 1GB box
that is not a stylistic choice: adding boto3 to this unit's memory ceiling
would cost more than the whole job does.

Three rules this file exists to hold:

  1. **Delete only after the row is confirmed.** The object is the ONLY copy.
     If the insert fails outside the transaction and the delete succeeds
     anyway, the turn is gone silently. So: insert, commit, read the row back,
     and only then delete.

  2. **Success is the row count moving, not the process exiting 0.** A run that
     lists ten objects, fails to store any, and exits cleanly is a failure that
     looks like a success in `systemctl status`.

  3. **`PRAGMA secure_delete` is per CONNECTION, not a property of the file.**
     Setting it once when the database is created does nothing for later runs —
     it has to be set on every open, or "destroyed after 90 days" is a claim
     about a NULL column while the old text still sits in a freed page.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

REGION = "auto"
SERVICE = "s3"
BUCKET = os.environ.get("CHATLOG_BUCKET", "airlens-chatlog")
PREFIX = "turn/"
DB_PATH = os.environ.get("CHATLOG_DB", "/var/lib/airlens/chatlog/chatlog.db")
# Matches the retention stated on /legal/privacy. Text only — the metadata
# columns stay, so quality trends survive the destruction of the text.
RETENTION_DAYS = 90
# One hourly slot's worth of work. The worker's own ceiling is 120 turns/hour,
# so this absorbs a backlog of several hours without unbounded memory.
MAX_OBJECTS_PER_RUN = 500
HTTP_TIMEOUT_SECONDS = 30

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_turn (
  id                  TEXT PRIMARY KEY,
  conversation_id     TEXT NOT NULL,
  turn_index          INTEGER NOT NULL,
  ts                  TEXT NOT NULL,
  locale              TEXT,
  page                TEXT,
  question            TEXT,
  answer              TEXT,
  intent              TEXT,
  guardrail_reason    TEXT,
  citations_json      TEXT,
  retrieval_top_score REAL,
  finish_reason       TEXT,
  answer_chars        INTEGER,
  degraded            INTEGER,
  model               TEXT,
  latency_ms          INTEGER,
  redacted_count      INTEGER,
  coords_truncated    INTEGER,
  sanitizer_version   TEXT NOT NULL,
  completion_status   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_turn_ts ON chat_turn (ts);
CREATE INDEX IF NOT EXISTS chat_turn_conversation ON chat_turn (conversation_id, turn_index);
"""

COLUMNS = [
    "id",
    "conversation_id",
    "turn_index",
    "ts",
    "locale",
    "page",
    "question",
    "answer",
    "intent",
    "guardrail_reason",
    "citations_json",
    "retrieval_top_score",
    "finish_reason",
    "answer_chars",
    "degraded",
    "model",
    "latency_ms",
    "redacted_count",
    "coords_truncated",
    "sanitizer_version",
    "completion_status",
]


def log(message: str) -> None:
    """Journal line. Never carries question or answer text — this box's journal
    is not covered by the sanitizer, and a debug print would put unredacted
    text into a second store with none of these rules."""
    print(f"[chatlog-pull] {message}", flush=True)


# ── credentials ─────────────────────────────────────────────────────────────
# One file, injected by systemd LoadCredential, holding three key=value lines:
#   account_id=…
#   access_key_id=…
#   secret_access_key=…
# The token must be scoped to this bucket alone. The same Cloudflare account
# holds airlens-models and airlens-captures; an account-scoped token would hand
# this VM both of those as well, for a job that only ever needs one prefix.


def load_credentials() -> dict[str, str]:
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    path = (
        os.path.join(creds_dir, "cf_r2_chatlog")
        if creds_dir
        else os.environ.get("CHATLOG_CREDENTIALS", "/etc/airlens/cf_r2_chatlog")
    )
    values: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [k for k in ("account_id", "access_key_id", "secret_access_key") if not values.get(k)]
    if missing:
        raise SystemExit(f"credential file {path} is missing: {', '.join(missing)}")
    return values


# ── S3 (SigV4) ──────────────────────────────────────────────────────────────


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def signed_request(creds: dict[str, str], method: str, key: str = "", query: dict[str, str] | None = None) -> bytes:
    host = f"{creds['account_id']}.r2.cloudflarestorage.com"
    canonical_uri = "/" + BUCKET
    if key:
        canonical_uri += "/" + urllib.parse.quote(key, safe="/~")
    query = query or {}
    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(query.items())
    )

    payload_hash = hashlib.sha256(b"").hexdigest()
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = _sign(("AWS4" + creds["secret_access_key"]).encode("utf-8"), date_stamp)
    signing_key = _sign(signing_key, REGION)
    signing_key = _sign(signing_key, SERVICE)
    signing_key = _sign(signing_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    url = f"https://{host}{canonical_uri}"
    if canonical_query:
        url += f"?{canonical_query}"
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={creds['access_key_id']}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def list_keys(creds: dict[str, str], limit: int) -> list[str]:
    body = signed_request(
        creds,
        "GET",
        query={"list-type": "2", "prefix": PREFIX, "max-keys": str(limit)},
    )
    root = ElementTree.fromstring(body)
    return [c.findtext(f"{S3_NS}Key") or "" for c in root.findall(f"{S3_NS}Contents")]


# ── SQLite ──────────────────────────────────────────────────────────────────


def connect() -> sqlite3.Connection:
    directory = os.path.dirname(DB_PATH)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # Per-connection, every time — see rule 3 in the module docstring.
    conn.execute("PRAGMA secure_delete=ON")
    # Deliberately NOT WAL. Under WAL, text nulled by the retention sweep can
    # survive in the -wal file until a checkpoint, which would make the
    # 90-day promise depend on a file nobody is watching.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(SCHEMA)
    conn.commit()
    os.chmod(DB_PATH, 0o600)
    return conn


def row_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM chat_turn").fetchone()[0])


def store(conn: sqlite3.Connection, record: dict) -> None:
    placeholders = ", ".join("?" for _ in COLUMNS)
    # OR IGNORE, not OR REPLACE: a re-pulled object (delete failed last run)
    # must not overwrite a row whose text the retention sweep already nulled.
    conn.execute(
        f"INSERT OR IGNORE INTO chat_turn ({', '.join(COLUMNS)}) VALUES ({placeholders})",
        [record.get(column) for column in COLUMNS],
    )
    conn.commit()


def is_stored(conn: sqlite3.Connection, turn_id: str) -> bool:
    return conn.execute("SELECT 1 FROM chat_turn WHERE id = ?", (turn_id,)).fetchone() is not None


def purge_expired(conn: sqlite3.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    cursor = conn.execute(
        "UPDATE chat_turn SET question = NULL, answer = NULL "
        "WHERE ts < ? AND (question IS NOT NULL OR answer IS NOT NULL)",
        (cutoff,),
    )
    purged = cursor.rowcount
    conn.commit()
    if purged:
        # Nulling a column frees the page but leaves the bytes until they are
        # overwritten; VACUUM with secure_delete on is what actually removes
        # them. Without this, "destroyed" is a description of the schema.
        conn.execute("VACUUM")
    return purged


# ── run ─────────────────────────────────────────────────────────────────────


def main() -> int:
    creds = load_credentials()
    conn = connect()
    try:
        before = row_count(conn)
        keys = list_keys(creds, MAX_OBJECTS_PER_RUN)
        handled = 0
        failures = 0

        for key in keys:
            try:
                body = signed_request(creds, "GET", key=key)
                record = json.loads(body)
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError) as err:
                # Leave the object alone: the bucket's 7-day lifecycle is the
                # backstop, and a malformed object is worth seeing again.
                failures += 1
                log(f"skip {key}: {type(err).__name__}")
                continue

            turn_id = record.get("id")
            if not turn_id:
                failures += 1
                log(f"skip {key}: record has no id")
                continue

            store(conn, record)
            if not is_stored(conn, turn_id):
                failures += 1
                log(f"NOT deleting {key}: row is not in the database after insert")
                continue

            try:
                signed_request(creds, "DELETE", key=key)
                handled += 1
            except (urllib.error.URLError, urllib.error.HTTPError) as err:
                # Safe direction: the row exists, so the next run re-pulls and
                # INSERT OR IGNORE makes it a no-op.
                log(f"stored {key} but delete failed ({type(err).__name__}) — will retry next run")

        after = row_count(conn)
        purged = purge_expired(conn)
        log(f"listed={len(keys)} stored_delta={after - before} handled={handled} failures={failures} purged={purged}")

        # Exiting 0 after doing nothing is the failure mode this guards against.
        if keys and handled == 0:
            log("FAILED: objects were listed but none could be stored and removed")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
