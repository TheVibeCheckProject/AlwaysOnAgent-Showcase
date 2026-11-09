"""
Database tables for the AlwaysOnAgent assistant.

Split out of core/memory.py §5 on 2026-11-03; MemoryManager inherits
these methods via _SchemaMixin, so _init_schema() still runs from __init__.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import DatabaseManager


class _SchemaMixin:
    # Host state, set by MemoryManager.__init__ (annotation-only: no runtime
    # value, so nothing here shadows a sibling mixin's attribute).
    db: "DatabaseManager"

    def _create_message_tables(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conv TEXT NOT NULL,
                role TEXT NOT NULL,              -- user | assistant | event
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                compressed INTEGER NOT NULL DEFAULT 0,
                embedding BLOB,
                embedded_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_mem_messages_conv ON mem_messages (conv, compressed, id);

            CREATE VIRTUAL TABLE IF NOT EXISTS mem_messages_fts
                USING fts5(content, content='mem_messages', content_rowid='id', tokenize='porter unicode61');
            CREATE TRIGGER IF NOT EXISTS mem_messages_ai AFTER INSERT ON mem_messages BEGIN
                INSERT INTO mem_messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_messages_ad AFTER DELETE ON mem_messages BEGIN
                INSERT INTO mem_messages_fts(mem_messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_messages_au AFTER UPDATE OF content ON mem_messages BEGIN
                INSERT INTO mem_messages_fts(mem_messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
                INSERT INTO mem_messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """)

    def _create_summary_tables(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conv TEXT NOT NULL,
                level INTEGER NOT NULL,
                content TEXT NOT NULL,
                start_msg_id INTEGER NOT NULL,
                end_msg_id INTEGER NOT NULL,
                start_at TEXT,
                end_at TEXT,
                created_at TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                rolled_up INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_mem_summaries ON mem_summaries (conv, rolled_up, level, start_msg_id);
            """)

    def _create_fact_tables(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'fact',
                importance INTEGER NOT NULL DEFAULT 3,
                pinned INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',   -- active | faded | superseded
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                valid_from TEXT,
                invalid_from TEXT,
                superseded_by INTEGER,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                embedding BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_mem_facts_status ON mem_facts (status, importance);

            CREATE VIRTUAL TABLE IF NOT EXISTS mem_facts_fts
                USING fts5(content, content='mem_facts', content_rowid='id', tokenize='porter unicode61');
            CREATE TRIGGER IF NOT EXISTS mem_facts_ai AFTER INSERT ON mem_facts BEGIN
                INSERT INTO mem_facts_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_facts_ad AFTER DELETE ON mem_facts BEGIN
                INSERT INTO mem_facts_fts(mem_facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_facts_au AFTER UPDATE OF content ON mem_facts BEGIN
                INSERT INTO mem_facts_fts(mem_facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
                INSERT INTO mem_facts_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """)

    def _create_fact_trigram(self, conn) -> None:
        conn.executescript("""
            -- Substring index for facts (names, domains, handles: "vibecheck" finds "example-site.com")
            CREATE VIRTUAL TABLE IF NOT EXISTS mem_facts_tri
                USING fts5(content, content='mem_facts', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER IF NOT EXISTS mem_facts_tri_ai AFTER INSERT ON mem_facts BEGIN
                INSERT INTO mem_facts_tri(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_facts_tri_ad AFTER DELETE ON mem_facts BEGIN
                INSERT INTO mem_facts_tri(mem_facts_tri, rowid, content) VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_facts_tri_au AFTER UPDATE OF content ON mem_facts BEGIN
                INSERT INTO mem_facts_tri(mem_facts_tri, rowid, content) VALUES ('delete', old.id, old.content);
                INSERT INTO mem_facts_tri(rowid, content) VALUES (new.id, new.content);
            END;
            """)

    def _migrate_schema_columns(self, conn) -> None:
        # Added later: summary embeddings, so only summaries relevant to a message are sent
        if "embedding" not in {r[1] for r in conn.execute("PRAGMA table_info(mem_summaries)")}:
            conn.execute("ALTER TABLE mem_summaries ADD COLUMN embedding BLOB")

        fact_cols = {r[1] for r in conn.execute("PRAGMA table_info(mem_facts)")}
        if "valid_from" not in fact_cols:
            conn.execute("ALTER TABLE mem_facts ADD COLUMN valid_from TEXT")
            conn.execute("UPDATE mem_facts SET valid_from = created_at WHERE valid_from IS NULL")
        if "invalid_from" not in fact_cols:
            conn.execute("ALTER TABLE mem_facts ADD COLUMN invalid_from TEXT")
        if "superseded_by" not in fact_cols:
            conn.execute("ALTER TABLE mem_facts ADD COLUMN superseded_by INTEGER")
        # Where a fact came from (IDEAS #19): the owner's own words that back it, and the message they were in
        if "evidence" not in fact_cols:
            conn.execute("ALTER TABLE mem_facts ADD COLUMN evidence TEXT")
        if "source_msg_id" not in fact_cols:
            conn.execute("ALTER TABLE mem_facts ADD COLUMN source_msg_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_facts_validity ON mem_facts (status, valid_from, invalid_from)")

    def _init_schema(self) -> None:
        conn = self.db.get_connection()
        with conn:
            self._create_message_tables(conn)
            self._create_summary_tables(conn)
            self._create_fact_tables(conn)
            self._create_fact_trigram(conn)
            self._migrate_schema_columns(conn)
