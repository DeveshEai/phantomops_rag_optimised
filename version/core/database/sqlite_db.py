"""
core/database/sqlite_db.py — SQLite backend (standalone, no DB server)

Activated by DB_TYPE=sqlite in .env. Stores everything in {DATABASE_DIR}/sally.db
— shared across version_N delivery folders sitting beside each other (same
convention as the shared .venv), so a new release connects to the same
accumulated history instead of starting over. Implements the SAME function
signatures as mysql_db.py + redis_db.py so the rest of the codebase routes
here transparently.

SQLite notes:
  - Text comparison is case-sensitive by default (Outlook IDs match exactly — no BINARY needed)
  - One writer at a time; WAL mode allows concurrent reads (fine for ~10 users)
  - AUTOINCREMENT id columns preserve insertion order → used for ORDER BY
"""

import os
import json
import time
import uuid
import sqlite3
from core.config import DATABASE_DIR, DB_FILENAME


def _db_file() -> str:
    os.makedirs(DATABASE_DIR, exist_ok=True)
    return os.path.join(DATABASE_DIR, DB_FILENAME)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_file(), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                thread_id  TEXT UNIQUE NOT NULL,
                sender     TEXT NOT NULL,
                created_at TEXT DEFAULT (DATETIME('now')),
                updated_at TEXT DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (DATETIME('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

            CREATE TABLE IF NOT EXISTS email_drafts (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id            TEXT NOT NULL,
                model_name            TEXT NOT NULL DEFAULT 'groq',
                thread_id             TEXT NOT NULL,
                message_id            TEXT NOT NULL,
                sender                TEXT NOT NULL,
                subject               TEXT NOT NULL,
                user_question         TEXT NOT NULL,
                ai_draft              TEXT NOT NULL,
                inbox_attachment_path TEXT,
                attachment_path       TEXT,
                agents_used           TEXT,
                status                TEXT DEFAULT 'pending'
                                      CHECK(status IN ('pending','sent','rejected','failed')),
                created_at            TEXT DEFAULT (DATETIME('now')),
                updated_at            TEXT DEFAULT (DATETIME('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                UNIQUE (message_id, model_name)
            );
            CREATE INDEX IF NOT EXISTS idx_drafts_status ON email_drafts (status, id);

            CREATE TABLE IF NOT EXISTS agent_executions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                message_id   TEXT NOT NULL,
                agent_name   TEXT NOT NULL,
                agent_input  TEXT,
                agent_output TEXT,
                duration_ms  INTEGER,
                status       TEXT DEFAULT 'success'
                             CHECK(status IN ('success','error','skipped')),
                created_at   TEXT DEFAULT (DATETIME('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id               TEXT PRIMARY KEY,
                task_number      INTEGER NOT NULL,
                thread_id        TEXT NOT NULL,
                session_id       TEXT NOT NULL,
                task_label       TEXT NOT NULL,
                deliverable_type TEXT NOT NULL DEFAULT 'general'
                                 CHECK(deliverable_type IN ('ppt','proposal','info','general')),
                status           TEXT DEFAULT 'open'
                                 CHECK(status IN ('open','delivered','in_revision','closed')),
                created_by       TEXT NOT NULL,
                output_path      TEXT,
                created_at       TEXT DEFAULT (DATETIME('now')),
                updated_at       TEXT DEFAULT (DATETIME('now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (thread_id, task_number)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_thread ON tasks (thread_id, task_number);

            CREATE TABLE IF NOT EXISTS task_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT NOT NULL,
                event_type TEXT NOT NULL
                           CHECK(event_type IN ('created','change_request','delivered','failed')),
                sender     TEXT NOT NULL,
                message    TEXT,
                model_name TEXT,
                file_path  TEXT,
                created_at TEXT DEFAULT (DATETIME('now')),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS config (
                key_name   TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS processing_claims (
                message_id TEXT PRIMARY KEY,
                claimed_at TEXT DEFAULT (DATETIME('now')),
                expires_at TEXT NOT NULL
            );

            -- Durable record of every message Sally has TERMINALLY handled (replied,
            -- observed, routed, rejected, skipped). Survives restart, so a message is
            -- never reprocessed/re-replied after a restart — independent of the
            -- watermark, the read/unread flag, or whether it produced a 'sent' draft.
            -- Only messages Sally actually handled are recorded, so downtime mail she
            -- never saw is still processed.
            CREATE TABLE IF NOT EXISTS handled_messages (
                message_id TEXT PRIMARY KEY,
                handled_at TEXT DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS session_cache (
                session_id TEXT PRIMARY KEY,
                history    TEXT NOT NULL,
                updated_at TEXT DEFAULT (DATETIME('now'))
            );

            -- RAG ingestion audit/dedup log. One row per source that has been
            -- chunked+embedded into the local Chroma KB (core/config.py
            -- CHROMA_PERSIST_DIR). content_hash lets a re-scan of an unchanged
            -- intake file or an unchanged web link be skipped as a no-op, while an
            -- edited source re-ingests (new hash -> new row, old chunks upserted).
            CREATE TABLE IF NOT EXISTS rag_sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type  TEXT NOT NULL CHECK(source_type IN ('intake_file','email','web')),
                source_ref   TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                chunk_count  INTEGER,
                language     TEXT,
                ingested_at  TEXT DEFAULT (DATETIME('now')),
                UNIQUE (source_type, source_ref, content_hash)
            );
        """)
        conn.commit()
        _run_migrations(conn)

        # Bound the durable handled-message log so it can't grow forever. Safe: the
        # poll only ever re-fetches mail within the lookback window (watermark advances
        # to ~now each clean poll), so entries older than that are never queried again.
        # 90 days is far beyond any realistic downtime, so dedup is never weakened.
        try:
            conn.execute(
                "DELETE FROM handled_messages WHERE handled_at < DATETIME('now','-90 days')"
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
    print("[SQLite] Tables ready.")


# ── Schema Migrations ─────────────────────────────────────────────────────────
# When a new version adds a COLUMN to an existing table, list the ALTER here.
# Each runs on every startup; SQLite raises "duplicate column" if it already
# exists, which we ignore — so it's safe to ship and re-run any number of times.
# New TABLES don't need an entry (CREATE TABLE IF NOT EXISTS above handles them).
#
# Example for a future v3 column:
#   "ALTER TABLE email_drafts ADD COLUMN priority TEXT DEFAULT 'normal'",
#
_MIGRATIONS = [
    # (add future column additions here — one ALTER statement per line)
]


def _run_migrations(conn):
    """Apply additive schema migrations idempotently (safe on every upgrade)."""
    applied = 0
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
            applied += 1
            print(f"[SQLite] Migration applied: {stmt[:60]}")
        except sqlite3.OperationalError as e:
            # "duplicate column name" → already applied on a previous run. Ignore.
            if "duplicate column" not in str(e).lower():
                print(f"[SQLite] Migration skipped ({e}): {stmt[:60]}")
        except Exception as e:
            print(f"[SQLite] Migration error ({e}): {stmt[:60]}")
    if applied:
        conn.commit()
        print(f"[SQLite] {applied} migration(s) applied.")


# ── Sessions ──────────────────────────────────────────────────────────────────

def get_or_create_session(thread_id: str, sender: str) -> str:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT id FROM sessions WHERE thread_id = ?", (thread_id,)).fetchone()
        if row:
            session_id = row[0]
            print(f"[SQLite] Existing session: {session_id}")
        else:
            session_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO sessions (id, thread_id, sender) VALUES (?,?,?)",
                    (session_id, thread_id, sender),
                )
                conn.commit()
                print(f"[SQLite] New session created: {session_id} for thread {thread_id}")
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id FROM sessions WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                session_id = row[0] if row else session_id
        return session_id
    finally:
        conn.close()


# ── Messages ──────────────────────────────────────────────────────────────────

def log_message(session_id: str, role: str, content: str):
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?,?,?)",
            (session_id, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_session_history(session_id: str) -> list:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]
    finally:
        conn.close()


# ── Email Drafts ──────────────────────────────────────────────────────────────

def save_email_draft(
    session_id, thread_id, message_id, sender, subject,
    user_question, ai_draft, model_name="supervisor",
    attachment_path=None, inbox_attachment_path=None,
    agents_used=None, status="pending",
) -> int:
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO email_drafts
            (session_id, model_name, thread_id, message_id, sender, subject,
             user_question, ai_draft, inbox_attachment_path, attachment_path,
             agents_used, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(message_id, model_name) DO UPDATE SET
                ai_draft              = excluded.ai_draft,
                attachment_path       = excluded.attachment_path,
                inbox_attachment_path = excluded.inbox_attachment_path,
                agents_used           = excluded.agents_used,
                status                = excluded.status,
                updated_at            = DATETIME('now')
        """, (session_id, model_name, thread_id, message_id, sender, subject,
              user_question, ai_draft, inbox_attachment_path, attachment_path,
              agents_used, status))
        row = conn.execute(
            "SELECT id FROM email_drafts WHERE message_id=? AND model_name=?",
            (message_id, model_name)
        ).fetchone()
        conn.commit()
        return row[0] if row else 0
    except Exception as e:
        conn.rollback()
        print(f"[SQLite] save_email_draft error: {e}")
        return 0
    finally:
        conn.close()


def save_agent_execution(session_id, message_id, agent_name, agent_input,
                         agent_output, duration_ms, status="success"):
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] Failed to log agent execution: {e}")
        return
    try:
        conn.execute(
            "INSERT INTO agent_executions "
            "(session_id,message_id,agent_name,agent_input,agent_output,duration_ms,status) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, message_id, agent_name, agent_input, agent_output, duration_ms, status),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] Failed to log agent execution: {e}")
    finally:
        conn.close()


def get_agent_executions(message_id: str) -> list:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT agent_name, agent_input, agent_output, duration_ms, status, created_at "
            "FROM agent_executions WHERE message_id = ? ORDER BY id ASC",
            (message_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def draft_exists_for_message(message_id: str) -> bool:
    conn = _get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM email_drafts WHERE message_id = ? AND status = 'sent'",
            (message_id,),
        ).fetchone()[0]
        return count > 0
    finally:
        conn.close()


def thread_has_sent_reply(thread_id: str) -> bool:
    """True if the agent has already replied in this thread — a "sticky" thread
    where follow-ups engage without the agent's name being repeated."""
    try:
        conn = _get_connection()
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM email_drafts WHERE thread_id = ? AND status = 'sent' LIMIT 1",
            (thread_id,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False
    finally:
        conn.close()


def get_thread_file_context(thread_id: str, model_name: str = None) -> dict:
    conn = _get_connection()
    try:
        if model_name:
            rows = conn.execute(
                "SELECT inbox_attachment_path, attachment_path FROM email_drafts "
                "WHERE thread_id=? AND model_name=? AND status='sent' ORDER BY id DESC",
                (thread_id, model_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT inbox_attachment_path, attachment_path FROM email_drafts "
                "WHERE thread_id=? AND status='sent' ORDER BY id DESC",
                (thread_id,),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"inbox_paths": [], "output_paths": []}

    def _parse(val):
        if not val:
            return []
        return [p.strip() for p in val.split(";") if p.strip() and os.path.isfile(p.strip())]

    output_paths = _parse(rows[0][1])
    seen, inbox_paths = set(), []
    for row in rows:
        for p in _parse(row[0]):
            if p not in seen:
                seen.add(p)
                inbox_paths.append(p)
    return {"inbox_paths": inbox_paths, "output_paths": output_paths}


def get_pending_drafts() -> list:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, session_id, model_name, thread_id, message_id, sender, subject, "
            "user_question, ai_draft, inbox_attachment_path, attachment_path, agents_used, created_at "
            "FROM email_drafts WHERE status='pending' ORDER BY id DESC, model_name ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_failed_drafts_for_message(message_id: str) -> list:
    """Return failed per-model drafts for an inbound message.

    These are used after an email send failure: retry should resend the saved
    draft output instead of re-running the agent pipeline and replaying side
    effects such as calendar invites or folder delivery.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, session_id, model_name, thread_id, message_id, sender, subject, "
            "user_question, ai_draft, inbox_attachment_path, attachment_path, agents_used, created_at "
            "FROM email_drafts WHERE message_id=? AND status='failed' ORDER BY id ASC, model_name ASC",
            (message_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_resumable_drafts_for_message(message_id: str) -> list:
    """Return drafts for an inbound message that were generated but never confirmed
    sent — status 'pending' (saved just before the send; a crash mid-send leaves
    them here) OR 'failed' (send raised). On a fresh poll, the presence of these
    means a PRIOR run already produced this reply and may or may not have sent it.
    The caller reconciles against the mailbox's Sent Items before re-sending, so
    the saved draft is resent (no regeneration, no replayed side effects) only
    when the original send did NOT go out — never producing a duplicate."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, session_id, model_name, thread_id, message_id, sender, subject, "
            "user_question, ai_draft, inbox_attachment_path, attachment_path, agents_used, created_at "
            "FROM email_drafts WHERE message_id=? AND status IN ('pending','failed') "
            "ORDER BY id ASC, model_name ASC",
            (message_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_message_drafts_sent(message_id: str) -> int:
    """Mark every unsent (pending/failed) draft for a message as 'sent'. Used by
    the crash-recovery reconciler when the reply is found already in Sent Items —
    so message-level dedup (draft_exists_for_message) suppresses any re-send."""
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE email_drafts SET status='sent', updated_at=DATETIME('now') "
            "WHERE message_id=? AND status IN ('pending','failed')",
            (message_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_draft_by_id(draft_id: int) -> dict:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM email_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_draft_status(draft_id: int, new_status: str, new_draft_text: str = None):
    conn = _get_connection()
    try:
        if new_draft_text is not None:
            conn.execute(
                "UPDATE email_drafts SET status=?, ai_draft=?, updated_at=DATETIME('now') WHERE id=?",
                (new_status, new_draft_text, draft_id),
            )
        else:
            conn.execute(
                "UPDATE email_drafts SET status=?, updated_at=DATETIME('now') WHERE id=?",
                (new_status, draft_id),
            )
        conn.commit()
    finally:
        conn.close()


def reject_other_model_drafts(message_id: str, approved_draft_id: int):
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE email_drafts SET status='rejected' "
            "WHERE message_id=? AND id!=? AND status='pending'",
            (message_id, approved_draft_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Phase 2: Task Management ──────────────────────────────────────────────────

def create_task(thread_id, session_id, task_label, deliverable_type, created_by) -> tuple:
    conn = _get_connection()
    try:
        task_number = conn.execute(
            "SELECT COALESCE(MAX(task_number),0)+1 FROM tasks WHERE thread_id=?",
            (thread_id,),
        ).fetchone()[0]
        task_id     = str(uuid.uuid4())
        valid_types = ("ppt", "proposal", "info", "general")
        safe_type   = deliverable_type if deliverable_type in valid_types else "general"
        conn.execute(
            "INSERT INTO tasks (id,task_number,thread_id,session_id,task_label,"
            "deliverable_type,created_by) VALUES (?,?,?,?,?,?,?)",
            (task_id, task_number, thread_id, session_id, task_label[:255], safe_type, created_by),
        )
        conn.commit()
        print(f"[SQLite] Task T{task_number} created: '{task_label}' ({safe_type})")
        return task_id, task_number
    except Exception as e:
        conn.rollback()
        print(f"[SQLite] create_task failed: {e}")
        raise
    finally:
        conn.close()


def get_task_by_tag(thread_id: str, task_number: int) -> dict:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE thread_id=? AND task_number=?",
            (thread_id, task_number),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_task(task_id: str) -> dict:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_open_tasks_for_thread(thread_id: str) -> list:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE thread_id=? AND status IN ('open','in_revision') "
            "ORDER BY task_number DESC",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_revisable_tasks_for_thread(thread_id: str) -> list:
    """Tasks a follow-up can still modify — includes 'delivered', so a
    'please revise this' after a deliverable was sent finds the live task
    instead of creating a new one. Excludes 'closed'."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE thread_id=? AND status IN ('open','in_revision','delivered') "
            "ORDER BY task_number DESC",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_task_output(task_id: str, output_path: str, status: str = "delivered"):
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE tasks SET output_path=?, status=?, updated_at=DATETIME('now') WHERE id=?",
            (output_path, status, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_task_event(task_id, event_type, sender, message=None, model_name=None, file_path=None):
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] log_task_event failed: {e}")
        return
    try:
        conn.execute(
            "INSERT INTO task_events (task_id,event_type,sender,message,model_name,file_path) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, event_type, sender, message, model_name, file_path),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] log_task_event failed: {e}")
    finally:
        conn.close()


# ── Cache / Config / Claims (replaces redis_db functions) ─────────────────────

def save_session_history(session_id: str, messages: list):
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] save_session_history failed: {e}")
        return
    try:
        conn.execute(
            "INSERT INTO session_cache (session_id, history) VALUES (?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET history=excluded.history, "
            "updated_at=DATETIME('now')",
            (session_id, json.dumps(messages)),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] save_session_history failed: {e}")
    finally:
        conn.close()


def load_session_history(session_id: str) -> list:
    try:
        conn = _get_connection()
    except Exception:
        return []
    try:
        row = conn.execute(
            "SELECT history FROM session_cache WHERE session_id=?", (session_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    finally:
        conn.close()
    return []


def clear_session(session_id: str):
    try:
        conn = _get_connection()
    except Exception:
        return
    try:
        conn.execute("DELETE FROM session_cache WHERE session_id=?", (session_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def claim_message(message_id: str, ttl_seconds: int = 600) -> bool:
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] claim_message failed ({e}) — proceeding.")
        return True
    try:
        conn.execute("DELETE FROM processing_claims WHERE expires_at < DATETIME('now')")
        conn.execute(
            "INSERT OR IGNORE INTO processing_claims (message_id, expires_at) "
            "VALUES (?, DATETIME('now', ? || ' seconds'))",
            (message_id, str(ttl_seconds)),
        )
        claimed = conn.execute("SELECT changes()").fetchone()[0] > 0
        conn.commit()
        return claimed
    except Exception as e:
        print(f"[SQLite] claim_message failed ({e}) — proceeding.")
        return True
    finally:
        conn.close()


def release_message(message_id: str):
    try:
        conn = _get_connection()
    except Exception:
        return
    try:
        conn.execute("DELETE FROM processing_claims WHERE message_id=?", (message_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def mark_message_handled(message_id: str):
    """Durably record that a message has been TERMINALLY handled (replied,
    observed, routed, rejected, or skipped) so it is never reprocessed after a
    restart. Idempotent. Best-effort: a failure here must not block handling."""
    if not message_id:
        return
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] mark_message_handled failed ({e}).")
        return
    try:
        conn.execute(
            "INSERT OR IGNORE INTO handled_messages (message_id) VALUES (?)",
            (message_id,),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] mark_message_handled failed ({e}).")
    finally:
        conn.close()


def is_message_handled(message_id: str) -> bool:
    """True if this message was already terminally handled in a previous run/poll."""
    if not message_id:
        return False
    try:
        conn = _get_connection()
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM handled_messages WHERE message_id=? LIMIT 1", (message_id,)
        ).fetchone()
        return bool(row)
    except Exception:
        return False
    finally:
        conn.close()


def set_config(key: str, value: str):
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] set_config({key}) failed: {e}")
        return
    try:
        conn.execute(
            "INSERT INTO config (key_name, value) VALUES (?,?) "
            "ON CONFLICT(key_name) DO UPDATE SET value=excluded.value, updated_at=DATETIME('now')",
            (key, value),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] set_config({key}) failed: {e}")
    finally:
        conn.close()


def get_config(key: str, default: str = "") -> str:
    try:
        conn = _get_connection()
    except Exception:
        return default
    try:
        row = conn.execute("SELECT value FROM config WHERE key_name=?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default
    finally:
        conn.close()


def get_all_config() -> dict:
    try:
        conn = _get_connection()
    except Exception:
        return {}
    try:
        rows = conn.execute("SELECT key_name, value FROM config").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def get_allowlist() -> list:
    raw = get_config("sender_allowlist", "")
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


def set_allowlist(entries: list):
    set_config("sender_allowlist", json.dumps(entries))


def set_pending_action(thread_id: str, kind: str, payload, ttl_seconds: int = 1800):
    """Stash a consequential action awaiting the user's confirmation in this
    thread (kind="schedule" or "cancel"). Stored in the config table with a
    timestamp so a never-confirmed action expires instead of lingering. Only one
    pending action per thread."""
    if not thread_id:
        return
    set_config(f"pending_action:{thread_id}",
               json.dumps({"ts": int(time.time()), "ttl": ttl_seconds,
                           "kind": kind, "payload": payload}))


def get_pending_action(thread_id: str):
    """Return {"kind", "payload"} for a thread's pending action, or None if none
    or expired."""
    if not thread_id:
        return None
    raw = get_config(f"pending_action:{thread_id}", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if int(time.time()) - int(data.get("ts", 0)) > int(data.get("ttl", 1800)):
            clear_pending_action(thread_id)
            return None
        return {"kind": data.get("kind"), "payload": data.get("payload")}
    except Exception:
        return None


def clear_pending_action(thread_id: str):
    if thread_id:
        set_config(f"pending_action:{thread_id}", "")


def set_retry_state(message_id: str, attempts: int, next_try_ts: int):
    """Remember a transient-provider-overload retry attempt for a message (see
    LLMTransientError) so the poll loop backs off before trying it again
    instead of hammering an already-overloaded provider every cycle. Stored in
    the config table — same pattern as set_pending_action, no schema change."""
    if not message_id:
        return
    set_config(f"retry_state:{message_id}",
               json.dumps({"attempts": attempts, "next_try_ts": next_try_ts}))


def get_retry_state(message_id: str):
    """Return {"attempts", "next_try_ts"} for a message's transient-retry
    ledger, or None if never recorded."""
    if not message_id:
        return None
    raw = get_config(f"retry_state:{message_id}", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return {"attempts": int(data.get("attempts", 0)),
                "next_try_ts": int(data.get("next_try_ts", 0))}
    except Exception:
        return None


def clear_retry_state(message_id: str):
    if message_id:
        set_config(f"retry_state:{message_id}", "")


def set_task_for_message(message_id: str, task: dict, ttl_seconds: int = 7200):
    """Remember which task a message minted, so a transient-retry re-attempt of
    the SAME email reuses that task instead of resolve_task_node's body-content
    heuristic creating a second, duplicate, orphaned task. TTL covers the retry
    window comfortably (worst case ~67 min across MAX_TRANSIENT_RETRIES) without
    lingering in the config table forever."""
    if not message_id:
        return
    set_config(f"task_for_message:{message_id}",
               json.dumps({"ts": int(time.time()), "ttl": ttl_seconds, "task": task}))


def get_task_for_message(message_id: str):
    """Return the task dict recorded by set_task_for_message, or None if never
    recorded or expired."""
    if not message_id:
        return None
    raw = get_config(f"task_for_message:{message_id}", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if int(time.time()) - int(data.get("ts", 0)) > int(data.get("ttl", 7200)):
            return None
        return data.get("task")
    except Exception:
        return None


def has_rag_source(source_type: str, source_ref: str, content_hash: str) -> bool:
    """True if this exact source content has already been ingested into the RAG
    KB — lets auto-ingest skip a no-op re-scan of an unchanged intake file."""
    try:
        conn = _get_connection()
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM rag_sources WHERE source_type=? AND source_ref=? AND content_hash=? LIMIT 1",
            (source_type, source_ref, content_hash),
        ).fetchone()
        return bool(row)
    except Exception:
        return False
    finally:
        conn.close()


def record_rag_source(source_type: str, source_ref: str, content_hash: str,
                       chunk_count: int, language: str = None):
    """Record a successful RAG ingestion (audit + dedup ledger). Best-effort:
    a failure here must not block the ingestion that already happened in Chroma."""
    try:
        conn = _get_connection()
    except Exception as e:
        print(f"[SQLite] record_rag_source failed ({e}).")
        return
    try:
        conn.execute(
            "INSERT OR IGNORE INTO rag_sources (source_type, source_ref, content_hash, chunk_count, language) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_type, source_ref, content_hash, chunk_count, language),
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] record_rag_source failed ({e}).")
    finally:
        conn.close()


def allowlist_configured_in_redis() -> bool:
    try:
        conn = _get_connection()
    except Exception:
        return False
    try:
        found = conn.execute(
            "SELECT 1 FROM config WHERE key_name='sender_allowlist' LIMIT 1"
        ).fetchone() is not None
        return found
    except Exception:
        return False
    finally:
        conn.close()


def ping() -> bool:
    try:
        conn = _get_connection()
    except Exception:
        return False
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        conn.close()
