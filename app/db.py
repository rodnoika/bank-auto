import hashlib
import os
import sqlite3
from contextlib import contextmanager


DB = os.getenv("DATABASE_PATH", "./data/bankhub.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 bank TEXT NOT NULL,
 account TEXT,
 external_id TEXT,
 operation_date TEXT,
 amount REAL NOT NULL,
 direction TEXT,
 currency TEXT DEFAULT 'KZT',
 counterparty_name TEXT,
 counterparty_bin TEXT,
 counterparty_iban TEXT,
 counterparty_bik TEXT,
 document_number TEXT,
 knp TEXT,
 kbe TEXT,
 payment_purpose TEXT,
 source_type TEXT NOT NULL,
 source_name TEXT,
 raw_text TEXT,
 raw_json TEXT,
 fingerprint TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'review',
 ai_summary TEXT,
 ai_category TEXT,
 ai_processed_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 exported_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tx_status ON transactions(status);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(operation_date);
"""

POSTGRES_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS transactions (
     id BIGSERIAL PRIMARY KEY,
     bank TEXT NOT NULL,
     account TEXT,
     external_id TEXT,
     operation_date TEXT,
     amount DOUBLE PRECISION NOT NULL,
     direction TEXT,
     currency TEXT DEFAULT 'KZT',
     counterparty_name TEXT,
     counterparty_bin TEXT,
     counterparty_iban TEXT,
     counterparty_bik TEXT,
     document_number TEXT,
     knp TEXT,
     kbe TEXT,
     payment_purpose TEXT,
     source_type TEXT NOT NULL,
     source_name TEXT,
     raw_text TEXT,
     raw_json TEXT,
     fingerprint TEXT NOT NULL UNIQUE,
     status TEXT NOT NULL DEFAULT 'review',
     ai_summary TEXT,
     ai_category TEXT,
     ai_processed_at TIMESTAMPTZ,
     created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
     exported_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_tx_status ON transactions(status)",
    "CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(operation_date)",
)

EDITABLE_FIELDS = {
    "operation_date", "amount", "direction", "currency", "counterparty_name",
    "counterparty_bin", "counterparty_iban", "counterparty_bik", "document_number", "knp", "kbe",
    "payment_purpose", "account",
}
BULK_CHUNK_SIZE = 400
COMPACT_TX_COLUMNS = (
    "id", "bank", "operation_date", "amount", "direction", "currency", "counterparty_name",
    "counterparty_bin", "payment_purpose", "source_name", "status", "ai_summary", "ai_category",
)


def is_postgres():
    return bool(DATABASE_URL)


def database_kind():
    return "postgresql" if is_postgres() else "sqlite"


@contextmanager
def conn():
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        if os.getenv("VERCEL"):
            raise RuntimeError("DATABASE_URL обязателен при запуске на Vercel")
        os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
        connection = sqlite3.connect(DB)
        connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def execute(connection, sql, params=()):
    if is_postgres():
        sql = sql.replace("?", "%s")
    return connection.execute(sql, params)


def init_db():
    with conn() as c:
        if is_postgres():
            for statement in POSTGRES_SCHEMA:
                c.execute(statement)
            for column, definition in {
                "ai_summary": "TEXT",
                "ai_category": "TEXT",
                "ai_processed_at": "TIMESTAMPTZ",
                "counterparty_bik": "TEXT",
            }.items():
                c.execute(f"ALTER TABLE transactions ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            c.executescript(SQLITE_SCHEMA)
            columns = {row["name"] for row in c.execute("PRAGMA table_info(transactions)")}
            migrations = {
                "ai_summary": "ALTER TABLE transactions ADD COLUMN ai_summary TEXT",
                "ai_category": "ALTER TABLE transactions ADD COLUMN ai_category TEXT",
                "ai_processed_at": "ALTER TABLE transactions ADD COLUMN ai_processed_at TEXT",
                "counterparty_bik": "ALTER TABLE transactions ADD COLUMN counterparty_bik TEXT",
            }
            for column, sql in migrations.items():
                if column not in columns:
                    c.execute(sql)

        # Старые записи MVP тоже должны пройти проверку перед передачей в 1С.
        execute(c, "UPDATE transactions SET status='review' WHERE status='new'")


def fingerprint(tx):
    stable = "|".join(str(tx.get(key) or "").strip().lower() for key in [
        "bank", "account", "external_id", "operation_date", "amount", "direction",
        "counterparty_bin", "counterparty_iban", "document_number", "payment_purpose",
    ])
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def insert_tx(tx):
    tx = dict(tx)
    tx["fingerprint"] = fingerprint(tx)
    tx["status"] = "review"
    columns = [
        "bank", "account", "external_id", "operation_date", "amount", "direction", "currency",
        "counterparty_name", "counterparty_bin", "counterparty_iban", "counterparty_bik", "document_number",
        "knp", "kbe", "payment_purpose", "source_type", "source_name", "raw_text", "raw_json",
        "fingerprint", "status",
    ]
    values = [tx.get(column) for column in columns]
    placeholders = ",".join(["?"] * len(columns))
    statement = f"INSERT INTO transactions ({','.join(columns)}) VALUES ({placeholders})"

    with conn() as c:
        if is_postgres():
            row = execute(
                c,
                statement + " ON CONFLICT (fingerprint) DO NOTHING RETURNING id",
                values,
            ).fetchone()
            if row:
                return row["id"], False
            row = execute(c, "SELECT id FROM transactions WHERE fingerprint=?", (tx["fingerprint"],)).fetchone()
            return row["id"], True

        try:
            cursor = c.execute(statement, values)
            return cursor.lastrowid, False
        except sqlite3.IntegrityError:
            row = c.execute(
                "SELECT id FROM transactions WHERE fingerprint=?", (tx["fingerprint"],)
            ).fetchone()
            return row["id"], True


def get_tx(tx_id):
    with conn() as c:
        row = execute(c, "SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
        return dict(row) if row else None


def _list_tx(connection, status=None, limit=500, compact=False):
    columns = ",".join(COMPACT_TX_COLUMNS) if compact else "*"
    if status:
        rows = execute(
            connection,
            f"SELECT {columns} FROM transactions WHERE status=? ORDER BY operation_date DESC,id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = execute(
            connection,
            f"SELECT {columns} FROM transactions ORDER BY operation_date DESC,id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_tx(status=None, limit=500, compact=False):
    with conn() as c:
        return _list_tx(c, status=status, limit=limit, compact=compact)


def _status_counts(connection):
    rows = execute(connection, "SELECT status, COUNT(*) AS total FROM transactions GROUP BY status").fetchall()
    result = {"review": 0, "ready": 0, "exported": 0, "ignored": 0}
    result.update({row["status"]: row["total"] for row in rows})
    return result


def status_counts():
    with conn() as c:
        return _status_counts(c)


def list_tx_with_counts(status=None, limit=500, compact=False):
    with conn() as c:
        return {
            "items": _list_tx(c, status=status, limit=limit, compact=compact),
            "counts": _status_counts(c),
        }


def update_tx(tx_id, changes):
    clean = {key: value for key, value in changes.items() if key in EDITABLE_FIELDS}
    if not clean:
        return get_tx(tx_id)
    assignments = ",".join(f"{key}=?" for key in clean)
    with conn() as c:
        exists = execute(c, "SELECT id FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if not exists:
            return None
        execute(
            c,
            f"UPDATE transactions SET {assignments} WHERE id=?",
            [*clean.values(), tx_id],
        )
    return get_tx(tx_id)


def set_status(tx_id, status, only_from=None):
    allowed = {"review", "ready", "exported", "ignored"}
    if status not in allowed:
        raise ValueError("Unsupported transaction status")
    with conn() as c:
        if only_from:
            cursor = execute(
                c,
                "UPDATE transactions SET status=? WHERE id=? AND status=?",
                (status, tx_id, only_from),
            )
        else:
            cursor = execute(c, "UPDATE transactions SET status=? WHERE id=?", (status, tx_id))
        return cursor.rowcount == 1


def approve_many(ids):
    clean_ids = list(dict.fromkeys(int(value) for value in ids))
    if not clean_ids:
        return 0
    approved = 0
    with conn() as c:
        for start in range(0, len(clean_ids), BULK_CHUNK_SIZE):
            chunk = clean_ids[start:start + BULK_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            cursor = execute(
                c,
                f"UPDATE transactions SET status='ready' WHERE status='review' AND id IN ({placeholders})",
                chunk,
            )
            approved += cursor.rowcount
    return approved


def delete_many(ids):
    clean_ids = list(dict.fromkeys(int(value) for value in ids))
    if not clean_ids:
        return 0
    if is_postgres():
        with conn() as c:
            cursor = execute(
                c,
                "DELETE FROM transactions WHERE id = ANY(?::bigint[])",
                (clean_ids,),
            )
            return cursor.rowcount
    deleted = 0
    with conn() as c:
        for start in range(0, len(clean_ids), BULK_CHUNK_SIZE):
            chunk = clean_ids[start:start + BULK_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            cursor = execute(
                c,
                f"DELETE FROM transactions WHERE id IN ({placeholders})",
                chunk,
            )
            deleted += cursor.rowcount
    return deleted


def save_ai_result(tx_id, summary, category):
    with conn() as c:
        execute(
            c,
            """UPDATE transactions
               SET ai_summary=?, ai_category=?, ai_processed_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (summary, category, tx_id),
        )


def mark_exported(tx_id):
    with conn() as c:
        cursor = execute(
            c,
            """UPDATE transactions
               SET status='exported', exported_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='ready'""",
            (tx_id,),
        )
        return cursor.rowcount == 1
