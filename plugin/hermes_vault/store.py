"""Local durable storage for the Hermes Vault provider.

The store deliberately has no Hermes imports.  That keeps the SQLite,
Markdown, JSONL, and recovery invariants testable outside a running agent and
lets the provider use the ``hermes_home`` selected by Hermes at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
from typing import Any, Iterable, Mapping, Sequence
import uuid

from .redaction import redact_object, redact_text


SCHEMA_VERSION = 1
MEMORY_KINDS = (
    "fact",
    "preference",
    "decision",
    "project",
    "entity",
    "runbook",
    "checkpoint",
    "reference",
    "observation",
)
MEMORY_STATUSES = ("candidate", "active", "superseded", "archived", "deleted")
SOURCE_TYPES = (
    "user_explicit",
    "local_observation",
    "verified_tool_result",
    "assistant_derived",
    "web",
    "browser",
    "external_mcp",
    "imported",
)
TRUST_LEVELS = ("high", "medium", "low", "untrusted")
UNTRUSTED_SOURCES = frozenset({"web", "browser", "external_mcp"})
TRUST_WEIGHTS = {"high": 1.5, "medium": 0.75, "low": 0.25, "untrusted": -0.5}
STATUS_WEIGHTS = {"active": 1.0, "candidate": 0.35, "archived": 0.1}
KIND_DIRECTORIES = {
    "fact": "facts",
    "preference": "facts",
    "decision": "decisions",
    "project": "projects",
    "entity": "entities",
    "runbook": "runbooks",
    "checkpoint": "checkpoints",
    "reference": "archive",
    "observation": "archive",
}


class VaultError(RuntimeError):
    """A user-correctable vault operation error."""


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    sqlite: str
    foreign_keys: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sqlite": self.sqlite,
            "foreign_keys": list(self.foreign_keys),
        }


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    """Replace a file atomically, flushing file and parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (text or "memory")[:72]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('fact','preference','decision','project','entity','runbook','checkpoint','reference','observation')),
    canonical_key TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate','active','superseded','archived','deleted')),
    source_type TEXT NOT NULL CHECK (source_type IN ('user_explicit','local_observation','verified_tool_result','assistant_derived','web','browser','external_mcp','imported')),
    source_ref TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT '{}',
    session_id TEXT NOT NULL DEFAULT '',
    trust TEXT NOT NULL CHECK (trust IN ('high','medium','low','untrusted')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    supersedes TEXT REFERENCES memories(id),
    superseded_at TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS memories_canonical_idx ON memories(canonical_key);
CREATE INDEX IF NOT EXISTS memories_status_idx ON memories(status);
CREATE INDEX IF NOT EXISTS memories_updated_idx ON memories(updated_at);
CREATE INDEX IF NOT EXISTS memories_supersedes_idx ON memories(supersedes);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    canonical_key, title, content, tags, source_ref,
    content='memories', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(rowid, canonical_key, title, content, tags, source_ref)
    VALUES (new.rowid, new.canonical_key, new.title, new.content, new.tags, new.source_ref);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, canonical_key, title, content, tags, source_ref)
    VALUES ('delete', old.rowid, old.canonical_key, old.title, old.content, old.tags, old.source_ref);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, canonical_key, title, content, tags, source_ref)
    VALUES ('delete', old.rowid, old.canonical_key, old.title, old.content, old.tags, old.source_ref);
    INSERT INTO memory_fts(rowid, canonical_key, title, content, tags, source_ref)
    VALUES (new.rowid, new.canonical_key, new.title, new.content, new.tags, new.source_ref);
END;
CREATE TABLE IF NOT EXISTS journal_events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    trust TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS journal_timestamp_idx ON journal_events(timestamp);
CREATE INDEX IF NOT EXISTS journal_session_idx ON journal_events(session_id);
CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
    event_type, content, source_ref,
    content='journal_events', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS journal_ai AFTER INSERT ON journal_events BEGIN
    INSERT INTO journal_fts(rowid, event_type, content, source_ref)
    VALUES (new.rowid, new.event_type, new.content, new.source_ref);
END;
CREATE TRIGGER IF NOT EXISTS journal_ad AFTER DELETE ON journal_events BEGIN
    INSERT INTO journal_fts(journal_fts, rowid, event_type, content, source_ref)
    VALUES ('delete', old.rowid, old.event_type, old.content, old.source_ref);
END;
CREATE TRIGGER IF NOT EXISTS journal_au AFTER UPDATE ON journal_events BEGIN
    INSERT INTO journal_fts(journal_fts, rowid, event_type, content, source_ref)
    VALUES ('delete', old.rowid, old.event_type, old.content, old.source_ref);
    INSERT INTO journal_fts(rowid, event_type, content, source_ref)
    VALUES (new.rowid, new.event_type, new.content, new.source_ref);
END;
"""


class VaultStore:
    """Thread-safe local store with SQLite as the structured index."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()
        os.chmod(self.root, 0o700)
        self._lock = threading.RLock()
        self._closed = False
        for directory in (
            "journal",
            "facts",
            "projects",
            "decisions",
            "runbooks",
            "entities",
            "checkpoints",
            "archive",
            "state",
        ):
            path = self.root / directory
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        self.db_path = self.root / "vault.db"
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._tighten_database_modes()
        self._initialize_schema()
        self._write_schema_state()
        self._ensure_manifest()
        self._ensure_readme()
        self._write_health()

    def _require_open(self) -> None:
        if self._closed:
            raise VaultError("vault database is closed")

    def _tighten_database_modes(self) -> None:
        for path in (self.db_path, Path(f"{self.db_path}-wal"), Path(f"{self.db_path}-shm")):
            if path.exists():
                os.chmod(path, 0o600)

    def _initialize_schema(self) -> None:
        with self._lock:
            self._require_open()
            self._conn.executescript(SCHEMA_SQL)
            row = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            current = int(row[0]) if row else 0
            if current > SCHEMA_VERSION:
                raise VaultError(
                    f"vault schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current < 1:
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, utc_now()),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            self._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
            self._conn.execute("INSERT INTO journal_fts(journal_fts) VALUES ('rebuild')")
            self._tighten_database_modes()

    def _write_schema_state(self) -> None:
        payload = {
            "schema": "hermes-vault",
            "schema_version": SCHEMA_VERSION,
            "sqlite_fts": "fts5",
            "updated_at": utc_now(),
        }
        _atomic_write(self.root / "state/schema.json", _json(payload) + "\n")

    def _read_state(self, name: str, default: Any) -> Any:
        path = self.root / "state" / name
        try:
            return _parse_json(path.read_text(encoding="utf-8"), default)
        except (FileNotFoundError, OSError):
            return default

    def _write_state(self, name: str, payload: Mapping[str, Any]) -> None:
        _atomic_write(self.root / "state" / name, _json(dict(payload)) + "\n")

    def _ensure_manifest(self) -> None:
        path = self.root / "state/manifest.json"
        if path.exists():
            return
        self._write_state(
            "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "created_at": utc_now(),
                "last_sync": None,
                "last_checkpoint": None,
                "last_backup": None,
            },
        )

    def _ensure_readme(self) -> None:
        path = self.root / "README.md"
        if not path.exists():
            _atomic_write(
                path,
                "# Hermes Vault\n\n"
                "Local profile-scoped durable memory. SQLite/FTS5 is the "
                "structured index; Markdown is the human-readable materialization; "
                "JSONL is the append-only evidence journal.\n\n"
                "This directory is private runtime state and must not be committed.\n",
            )

    def _write_health(self, integrity: IntegrityResult | None = None) -> None:
        result = integrity or self.integrity()
        manifest = self._read_state("manifest.json", {})
        counts = self._counts()
        self._write_state(
            "health.json",
            {
                "ok": result.ok,
                "sqlite_integrity": result.sqlite,
                "foreign_keys": list(result.foreign_keys),
                "schema_version": SCHEMA_VERSION,
                "item_counts": counts,
                "pending_writes": 0,
                "last_durable_turn": manifest.get("last_sync"),
                "last_checkpoint": manifest.get("last_checkpoint"),
                "last_backup": manifest.get("last_backup"),
                "updated_at": utc_now(),
            },
        )

    def _update_manifest(self, **updates: Any) -> None:
        manifest = self._read_state("manifest.json", {})
        manifest.update(updates)
        manifest["schema_version"] = SCHEMA_VERSION
        self._write_state("manifest.json", manifest)

    def _counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM memories GROUP BY status"
            ).fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def integrity(self) -> IntegrityResult:
        with self._lock:
            self._require_open()
            sqlite_result = str(self._conn.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = tuple(
                {"table": row[0], "rowid": row[1], "parent": row[2], "fkid": row[3]}
                for row in self._conn.execute("PRAGMA foreign_key_check").fetchall()
            )
            return IntegrityResult(
                ok=sqlite_result.lower() == "ok" and not foreign_keys,
                sqlite=sqlite_result,
                foreign_keys=foreign_keys,
            )

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["provenance"] = _parse_json(result.get("provenance"), {})
        result["tags"] = _parse_json(result.get("tags"), [])
        result["metadata"] = _parse_json(result.get("metadata"), {})
        result["markdown_path"] = str(self._markdown_path(result))
        return result

    def get(
        self,
        memory_id: str,
        *,
        include_deleted: bool = False,
        include_superseded: bool = True,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._require_open()
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return None
            if not include_deleted and row["status"] == "deleted":
                return None
            if not include_superseded and row["status"] == "superseded":
                return None
            return self._row_to_dict(row)

    @staticmethod
    def _validate_choice(name: str, value: str, choices: Iterable[str]) -> str:
        if value not in choices:
            raise VaultError(f"invalid {name}: {value!r}")
        return value

    @staticmethod
    def _normalize_tags(tags: Sequence[str] | str | None) -> list[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            values = [item.strip() for item in tags.split(",")]
        else:
            values = [str(item).strip() for item in tags]
        clean = []
        for value in values:
            if value and value not in clean:
                clean.append(value[:80])
        return clean[:32]

    def _sanitize_memory_input(
        self,
        *,
        kind: str,
        canonical_key: str,
        title: str,
        content: str,
        status: str,
        source_type: str,
        source_ref: str,
        provenance: Mapping[str, Any] | None,
        session_id: str,
        trust: str | None,
        confidence: float,
        tags: Sequence[str] | str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        kind = self._validate_choice("kind", kind, MEMORY_KINDS)
        status = self._validate_choice("status", status, MEMORY_STATUSES)
        source_type = self._validate_choice("source_type", source_type, SOURCE_TYPES)
        canonical_result = redact_text(canonical_key.strip())
        title_result = redact_text(title.strip())
        content_result = redact_text(content)
        source_ref_result = redact_text(source_ref.strip())
        if not canonical_result.text:
            raise VaultError("canonical_key must not be empty")
        if not title_result.text:
            raise VaultError("title must not be empty")
        if not content_result.text.strip():
            raise VaultError("content must not be empty")
        if len(canonical_result.text) > 240:
            raise VaultError("canonical_key is too long")
        if len(title_result.text) > 240:
            raise VaultError("title is too long")
        if len(content_result.text) > 100_000:
            raise VaultError("content is too long")
        if not 0.0 <= float(confidence) <= 1.0:
            raise VaultError("confidence must be between 0 and 1")
        clean_provenance, provenance_patterns = redact_object(dict(provenance or {}))
        clean_metadata, metadata_patterns = redact_object(dict(metadata or {}))
        redaction_patterns = list(
            dict.fromkeys(
                canonical_result.patterns
                + title_result.patterns
                + content_result.patterns
                + source_ref_result.patterns
                + provenance_patterns
                + metadata_patterns
            )
        )
        clean_metadata = dict(clean_metadata)
        if redaction_patterns:
            clean_metadata["redaction"] = {
                "applied": True,
                "patterns": redaction_patterns,
            }
        selected_trust = trust or {
            "user_explicit": "high",
            "local_observation": "high",
            "verified_tool_result": "high",
            "assistant_derived": "medium",
            "web": "untrusted",
            "browser": "untrusted",
            "external_mcp": "untrusted",
            "imported": "medium",
        }[source_type]
        selected_trust = self._validate_choice("trust", selected_trust, TRUST_LEVELS)
        if source_type in UNTRUSTED_SOURCES and selected_trust != "untrusted":
            selected_trust = "untrusted"
            clean_metadata["trust_adjusted"] = True
        normalized_tags, tag_patterns = redact_object(self._normalize_tags(tags))
        if tag_patterns:
            current_redaction = clean_metadata.get("redaction")
            current_patterns = (
                current_redaction.get("patterns", [])
                if isinstance(current_redaction, dict)
                else []
            )
            clean_metadata["redaction"] = {
                "applied": True,
                "patterns": list(
                    dict.fromkeys(
                        list(current_patterns)
                        + list(tag_patterns)
                    )
                ),
            }
        return {
            "kind": kind,
            "canonical_key": canonical_result.text,
            "title": title_result.text,
            "content": content_result.text,
            "status": status,
            "source_type": source_type,
            "source_ref": source_ref_result.text,
            "provenance": clean_provenance,
            "session_id": redact_text(session_id).text[:240],
            "trust": selected_trust,
            "confidence": float(confidence),
            "tags": normalized_tags,
            "metadata": clean_metadata,
            "content_hash": hashlib.sha256(content_result.text.encode("utf-8")).hexdigest(),
        }

    def upsert(
        self,
        *,
        kind: str,
        canonical_key: str,
        title: str,
        content: str,
        status: str = "active",
        source_type: str = "assistant_derived",
        source_ref: str = "",
        provenance: Mapping[str, Any] | None = None,
        session_id: str = "",
        trust: str | None = None,
        confidence: float = 0.5,
        tags: Sequence[str] | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a record, superseding the active same-key record."""

        clean = self._sanitize_memory_input(
            kind=kind,
            canonical_key=canonical_key,
            title=title,
            content=content,
            status=status,
            source_type=source_type,
            source_ref=source_ref,
            provenance=provenance,
            session_id=session_id,
            trust=trust,
            confidence=confidence,
            tags=tags,
            metadata=metadata,
        )
        now = utc_now()
        target_id = memory_id or uuid.uuid4().hex
        superseded_ids: list[str] = []
        old_row: sqlite3.Row | None = None
        action = "created"
        with self._lock:
            self._require_open()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if memory_id:
                    old_row = self._conn.execute(
                        "SELECT * FROM memories WHERE id = ?", (memory_id,)
                    ).fetchone()
                    if old_row is None:
                        raise VaultError(f"memory not found: {memory_id}")
                    action = "updated"
                    self._conn.execute(
                        """UPDATE memories SET kind=?, canonical_key=?, title=?, content=?,
                           status=?, source_type=?, source_ref=?, provenance=?, session_id=?,
                           trust=?, confidence=?, updated_at=?, tags=?, content_hash=?, metadata=?
                           WHERE id=?""",
                        (
                            clean["kind"],
                            clean["canonical_key"],
                            clean["title"],
                            clean["content"],
                            clean["status"],
                            clean["source_type"],
                            clean["source_ref"],
                            _json(clean["provenance"]),
                            clean["session_id"],
                            clean["trust"],
                            clean["confidence"],
                            now,
                            _json(clean["tags"]),
                            clean["content_hash"],
                            _json(clean["metadata"]),
                            memory_id,
                        ),
                    )
                else:
                    duplicate = self._conn.execute(
                        """SELECT * FROM memories
                           WHERE canonical_key=? AND content_hash=?
                             AND status IN ('active','candidate','archived')
                           ORDER BY updated_at DESC LIMIT 1""",
                        (clean["canonical_key"], clean["content_hash"]),
                    ).fetchone()
                    if duplicate is not None:
                        self._conn.execute("COMMIT")
                        return self._row_to_dict(duplicate)
                    active_rows = self._conn.execute(
                        """SELECT * FROM memories
                           WHERE canonical_key=? AND status='active'
                           ORDER BY updated_at DESC""",
                        (clean["canonical_key"],),
                    ).fetchall()
                    for active in active_rows:
                        old_row = old_row or active
                        superseded_ids.append(str(active["id"]))
                        self._conn.execute(
                            """UPDATE memories SET status='superseded', valid_to=?,
                               superseded_at=?, updated_at=? WHERE id=?""",
                            (now, now, now, active["id"]),
                        )
                    self._conn.execute(
                        """INSERT INTO memories(
                           id, kind, canonical_key, title, content, status, source_type,
                           source_ref, provenance, session_id, trust, confidence, created_at,
                           updated_at, valid_from, tags, content_hash, metadata, supersedes
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            target_id,
                            clean["kind"],
                            clean["canonical_key"],
                            clean["title"],
                            clean["content"],
                            clean["status"],
                            clean["source_type"],
                            clean["source_ref"],
                            _json(clean["provenance"]),
                            clean["session_id"],
                            clean["trust"],
                            clean["confidence"],
                            now,
                            now,
                            now,
                            _json(clean["tags"]),
                            clean["content_hash"],
                            _json(clean["metadata"]),
                            old_row["id"] if old_row is not None else None,
                        ),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._tighten_database_modes()
            row = self._conn.execute("SELECT * FROM memories WHERE id=?", (target_id,)).fetchone()
            if row is None:
                raise VaultError("memory write committed without a readable row")
            result = self._row_to_dict(row)
        for old_id in superseded_ids:
            old = self.get(old_id, include_deleted=True)
            if old:
                self._materialize(old)
                self._record_journal(
                    "memory_superseded",
                    session_id=old.get("session_id", ""),
                    source_type="local_observation",
                    source_ref=clean["canonical_key"],
                    trust="high",
                    content=f"memory {old_id} superseded by {target_id}",
                    related_memory_ids=[old_id, target_id],
                    metadata={"canonical_key": clean["canonical_key"]},
                )
        self._materialize(result)
        self._record_journal(
            f"memory_{action}",
            session_id=clean["session_id"],
            source_type=clean["source_type"],
            source_ref=clean["source_ref"],
            trust=clean["trust"],
            content=clean["content"],
            related_memory_ids=[target_id],
            metadata={"kind": clean["kind"], "canonical_key": clean["canonical_key"]},
        )
        self._write_health()
        return result

    def supersede(self, old_id: str, new_id: str) -> dict[str, Any]:
        with self._lock:
            old = self._conn.execute("SELECT * FROM memories WHERE id=?", (old_id,)).fetchone()
            new = self._conn.execute("SELECT * FROM memories WHERE id=?", (new_id,)).fetchone()
            if old is None or new is None:
                raise VaultError("both old_id and new_id must identify memories")
            now = utc_now()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """UPDATE memories SET status='superseded', valid_to=?,
                       superseded_at=?, updated_at=? WHERE id=?""",
                    (now, now, now, old_id),
                )
                self._conn.execute(
                    "UPDATE memories SET supersedes=?, updated_at=? WHERE id=?",
                    (old_id, now, new_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        old_result = self.get(old_id, include_deleted=True)
        new_result = self.get(new_id, include_deleted=True)
        if old_result:
            self._materialize(old_result)
        if new_result:
            self._materialize(new_result)
        self._record_journal(
            "memory_superseded",
            session_id=new_result.get("session_id", "") if new_result else "",
            source_type="local_observation",
            source_ref="explicit supersession",
            trust="high",
            content=f"memory {old_id} superseded by {new_id}",
            related_memory_ids=[old_id, new_id],
            metadata={},
        )
        self._write_health()
        return new_result or {}

    def forget(self, memory_id: str, *, hard_delete: bool = False, confirm: str = "") -> bool:
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                return False
            if hard_delete and confirm != "FORGET":
                raise VaultError("hard deletion requires confirm='FORGET'")
            if hard_delete:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            else:
                now = utc_now()
                self._conn.execute(
                    """UPDATE memories SET status='deleted', content='[FORGOTTEN]',
                       content_hash=?, metadata=?, updated_at=?, valid_to=? WHERE id=?""",
                    (
                        hashlib.sha256(b"[FORGOTTEN]").hexdigest(),
                        _json({"forgotten_at": now}),
                        now,
                        now,
                        memory_id,
                    ),
                )
        if hard_delete:
            try:
                self._materialize_path_for_row(row, delete=True)
            except OSError:
                pass
        else:
            current = self.get(memory_id, include_deleted=True)
            if current:
                self._materialize(current)
        self._record_journal(
            "memory_deleted",
            session_id=str(row["session_id"]),
            source_type="local_observation",
            source_ref="forget",
            trust="high",
            content=f"memory {memory_id} forgotten",
            related_memory_ids=[memory_id],
            metadata={"hard_delete": hard_delete},
        )
        self._write_health()
        return True

    def _search_match(self, query: str) -> str:
        terms = re.findall(r"[\w.-]+", query, flags=re.UNICODE)
        if not terms:
            return ""
        return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:24])

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        include_superseded: bool = False,
        include_deleted: bool = False,
        include_candidates: bool = True,
        include_archived: bool = False,
        min_trust: str | None = None,
    ) -> list[dict[str, Any]]:
        match = self._search_match(query)
        if not match:
            return []
        limit = max(1, min(int(limit), 50))
        statuses = ["active"]
        if include_candidates:
            statuses.append("candidate")
        if include_superseded:
            statuses.append("superseded")
        if include_archived:
            statuses.append("archived")
        if include_deleted:
            statuses.append("deleted")
        trust_floor = None
        if min_trust is not None:
            trust_floor = self._validate_choice("min_trust", min_trust, TRUST_LEVELS)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.*, bm25(memory_fts) AS bm25_rank
                    FROM memory_fts JOIN memories AS m ON m.rowid=memory_fts.rowid
                   WHERE memory_fts MATCH ?
                     AND m.status IN ({','.join('?' for _ in statuses)})""",
                (match, *statuses),
            ).fetchall()
        query_lower = query.casefold().strip()
        terms = {term.casefold() for term in re.findall(r"[\w.-]+", query)}
        ranked: list[dict[str, Any]] = []
        for row in rows:
            if trust_floor and TRUST_LEVELS.index(row["trust"]) > TRUST_LEVELS.index(trust_floor):
                continue
            result = self._row_to_dict(row)
            score = -float(row["bm25_rank"] or 0.0)
            canonical = str(row["canonical_key"]).casefold()
            if canonical == query_lower:
                score += 4.0
            elif query_lower and query_lower in canonical:
                score += 2.0
            tags = {str(tag).casefold() for tag in result["tags"]}
            if terms & tags:
                score += 1.0
            score += TRUST_WEIGHTS.get(row["trust"], 0.0)
            score += STATUS_WEIGHTS.get(row["status"], 0.0)
            try:
                updated = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
                age_days = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 86400)
            except ValueError:
                age_days = 365.0
            score += max(0.0, 0.5 * (1.0 - min(age_days, 365.0) / 365.0))
            if str(row["kind"]).casefold() in terms:
                score += 0.4
            result["score"] = round(score, 6)
            result.pop("bm25_rank", None)
            ranked.append(result)
        ranked.sort(key=lambda item: (-item["score"], item["updated_at"], item["id"]))
        return ranked[:limit]

    def search_journal(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        match = self._search_match(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                """SELECT j.*, bm25(journal_fts) AS bm25_rank
                     FROM journal_fts JOIN journal_events AS j ON j.rowid=journal_fts.rowid
                    WHERE journal_fts MATCH ? ORDER BY bm25_rank LIMIT ?""",
                (match, max(1, min(int(limit), 100))),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["related_memory_ids"] = _parse_json(item.get("related_memory_ids"), [])
            item["metadata"] = _parse_json(item.get("metadata"), {})
            item.pop("bm25_rank", None)
            results.append(item)
        return results

    def _markdown_path(self, row: Mapping[str, Any]) -> Path:
        directory = KIND_DIRECTORIES.get(str(row["kind"]), "archive")
        return self.root / directory / f"{_slug(str(row['canonical_key']))}-{str(row['id'])[:12]}.md"

    def _materialize_path_for_row(self, row: Mapping[str, Any], *, delete: bool = False) -> None:
        path = self._markdown_path(row)
        if delete:
            path.unlink(missing_ok=True)
            return
        lines = [
            "---",
            f"id: {json.dumps(str(row['id']))}",
            f"kind: {json.dumps(str(row['kind']))}",
            f"canonical_key: {json.dumps(str(row['canonical_key']))}",
            f"status: {json.dumps(str(row['status']))}",
            f"source_type: {json.dumps(str(row['source_type']))}",
            f"source_ref: {json.dumps(str(row['source_ref']))}",
            f"trust: {json.dumps(str(row['trust']))}",
            f"confidence: {float(row['confidence']):.6f}",
            f"session_id: {json.dumps(str(row['session_id']))}",
            f"created_at: {json.dumps(str(row['created_at']))}",
            f"updated_at: {json.dumps(str(row['updated_at']))}",
            f"valid_from: {json.dumps(str(row['valid_from']))}",
            f"valid_to: {json.dumps(row['valid_to'])}",
            f"supersedes: {json.dumps(row['supersedes'])}",
            f"tags: {_json(row['tags'] if isinstance(row['tags'], list) else _parse_json(row['tags'], []))}",
            f"metadata: {_json(row['metadata'] if isinstance(row['metadata'], dict) else _parse_json(row['metadata'], {}))}",
            "---",
            "",
            f"# {str(row['title']).replace(chr(10), ' ')}",
            "",
            str(row["content"]),
            "",
        ]
        _atomic_write(path, "\n".join(lines), 0o600)

    def _materialize(self, row: Mapping[str, Any]) -> None:
        self._materialize_path_for_row(row)

    def _repair_jsonl(self, path: Path) -> None:
        if not path.exists():
            return
        with path.open("r+b") as handle:
            data = handle.read()
            if data and not data.endswith(b"\n"):
                last_newline = data.rfind(b"\n")
                handle.truncate(max(0, last_newline + 1))
                handle.flush()
                os.fsync(handle.fileno())

    def _append_jsonl(self, payload: Mapping[str, Any], timestamp: str) -> None:
        date = timestamp[:10].split("-")
        path = self.root / "journal" / date[0] / date[1] / f"{date[2]}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        with self._lock:
            self._repair_jsonl(path)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                data = (_json(dict(payload)) + "\n").encode("utf-8")
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)

    def _record_journal(
        self,
        event_type: str,
        *,
        session_id: str = "",
        source_type: str = "local_observation",
        source_ref: str = "",
        trust: str = "medium",
        content: str = "",
        related_memory_ids: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        source_type = self._validate_choice("source_type", source_type, SOURCE_TYPES)
        trust = self._validate_choice("trust", trust, TRUST_LEVELS)
        result = redact_text(content)
        clean_metadata, metadata_patterns = redact_object(dict(metadata or {}))
        clean_metadata = dict(clean_metadata)
        if result.redacted or metadata_patterns:
            clean_metadata["redaction"] = {
                "applied": True,
                "patterns": list(dict.fromkeys(list(result.patterns) + list(metadata_patterns))),
            }
        timestamp = utc_now()
        event_id = uuid.uuid4().hex
        clean_session = redact_text(session_id).text[:240]
        clean_ref = redact_text(source_ref).text[:500]
        clean_related = [redact_text(item).text[:120] for item in related_memory_ids]
        payload = {
            "timestamp": timestamp,
            "event_id": event_id,
            "session_id": clean_session,
            "event_type": event_type,
            "source_type": source_type,
            "source_ref": clean_ref,
            "trust": trust,
            "content": result.text,
            "content_hash": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            "related_memory_ids": clean_related,
            "metadata": clean_metadata,
        }
        self._append_jsonl(payload, timestamp)
        with self._lock:
            self._conn.execute(
                """INSERT INTO journal_events(
                    event_id, timestamp, session_id, event_type, source_type, source_ref,
                    trust, content, content_hash, related_memory_ids, metadata
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    timestamp,
                    clean_session,
                    event_type,
                    source_type,
                    clean_ref,
                    trust,
                    result.text,
                    payload["content_hash"],
                    _json(clean_related),
                    _json(clean_metadata),
                ),
            )
            self._tighten_database_modes()
        return event_id

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Sequence[Mapping[str, Any]] | None = None,
    ) -> str:
        user = redact_text(user_content).text
        assistant = redact_text(assistant_content).text
        content = f"USER:\n{user}\n\nASSISTANT:\n{assistant}".strip()
        event_id = self._record_journal(
            "turn",
            session_id=session_id,
            source_type="assistant_derived",
            source_ref="hermes turn evidence",
            trust="medium",
            content=content,
            metadata={
                "message_count": len(messages) if messages is not None else None,
                "curated_memory": False,
            },
        )
        timestamp = utc_now()
        self._update_manifest(last_sync=timestamp)
        self._write_health()
        return event_id

    def record_event(
        self,
        event_type: str,
        *,
        session_id: str = "",
        source_type: str = "local_observation",
        source_ref: str = "",
        trust: str = "medium",
        content: str = "",
        related_memory_ids: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append a redacted non-turn event to both journal representations."""

        event_id = self._record_journal(
            event_type,
            session_id=session_id,
            source_type=source_type,
            source_ref=source_ref,
            trust=trust,
            content=content,
            related_memory_ids=related_memory_ids,
            metadata=metadata,
        )
        self._write_health()
        return event_id

    def session_end(self, session_id: str, message_count: int) -> str:
        event_id = self.record_event(
            "session_end",
            session_id=session_id,
            source_type="local_observation",
            source_ref="Hermes session boundary",
            trust="high",
            content="session ended",
            metadata={"message_count": int(message_count)},
        )
        self._write_health(self.integrity())
        return event_id

    @staticmethod
    def _direct_evidence(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        evidence = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            if message.get("_compressed_summary"):
                continue
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            evidence.append({"role": str(role), "content": content})
        return evidence

    def checkpoint(
        self,
        name: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        evidence = self._direct_evidence(messages)
        clean_evidence = [
            {"role": item["role"], "content": redact_text(item["content"]).text}
            for item in evidence
        ]
        serialized = _json(clean_evidence)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        canonical_key = f"checkpoint:{digest}"
        checkpoint_content = "\n\n".join(
            f"{item['role'].upper()}:\n{item['content']}" for item in clean_evidence
        ) or "(empty direct evidence)"
        checkpoint_hash = hashlib.sha256(checkpoint_content.encode("utf-8")).hexdigest()
        with self._lock:
            existing = self._conn.execute(
                """SELECT * FROM memories WHERE canonical_key=? AND content_hash=?
                   ORDER BY updated_at DESC LIMIT 1""",
                (canonical_key, checkpoint_hash),
            ).fetchone()
        if existing is not None:
            result = self._row_to_dict(existing)
        else:
            result = self.upsert(
                kind="checkpoint",
                canonical_key=canonical_key,
                title=name or "Pre-compression checkpoint",
                content=checkpoint_content,
                status="archived",
                source_type="local_observation",
                source_ref="pre-compress",
                provenance={"checkpoint": True, "api_version": 2},
                session_id=session_id,
                trust="high",
                confidence=1.0,
                metadata={"evidence_digest": digest, "message_count": len(clean_evidence)},
            )
        timestamp = utc_now()
        self._update_manifest(last_checkpoint=timestamp)
        self._record_journal(
            "checkpoint",
            session_id=session_id,
            source_type="local_observation",
            source_ref="pre-compress",
            trust="high",
            content=f"checkpoint {result['id']} completed",
            related_memory_ids=[result["id"]],
            metadata={"name": name or "Pre-compression checkpoint", "evidence_digest": digest},
        )
        self._write_health()
        return result

    def _record_backup(self, destination: Path) -> None:
        timestamp = utc_now()
        self._update_manifest(last_backup=timestamp)
        self._record_journal(
            "backup",
            source_type="local_observation",
            source_ref=str(destination),
            trust="high",
            content="vault backup completed",
            metadata={"destination": str(destination)},
        )
        self._write_health()

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        """Create a consistent SQLite backup plus Markdown/journal/state copy."""

        destination_path = Path(destination).expanduser().resolve()
        destination_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination_path, 0o700)
        db_copy = destination_path / "vault.db"
        with self._lock:
            self._require_open()
            backup_conn = sqlite3.connect(db_copy)
            try:
                self._conn.backup(backup_conn)
                backup_conn.commit()
            finally:
                backup_conn.close()
            os.chmod(db_copy, 0o600)
            self._tighten_database_modes()
        for name in (
            "journal",
            "facts",
            "projects",
            "decisions",
            "runbooks",
            "entities",
            "checkpoints",
            "archive",
            "state",
        ):
            source = self.root / name
            target = destination_path / name
            if source.exists():
                shutil.copytree(source, target, dirs_exist_ok=True)
                for path in target.rglob("*"):
                    if path.is_dir():
                        os.chmod(path, 0o700)
                    else:
                        os.chmod(path, 0o600)
        _atomic_write(
            destination_path / "MANIFEST.json",
            _json({
                "created_at": utc_now(),
                "source": str(self.root),
                "schema_version": SCHEMA_VERSION,
                "browser_profile": "excluded",
            }) + "\n",
        )
        self._record_backup(destination_path)
        return destination_path

    def reindex(self) -> IntegrityResult:
        with self._lock:
            self._require_open()
            self._conn.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
            self._conn.execute("INSERT INTO journal_fts(journal_fts) VALUES ('rebuild')")
            self._tighten_database_modes()
        result = self.integrity()
        self._record_journal(
            "integrity_check",
            source_type="local_observation",
            source_ref="reindex",
            trust="high",
            content=f"vault reindex completed: {result.sqlite}",
            metadata=result.as_dict(),
        )
        self._write_health(result)
        return result

    def cleanup_temporary_files(self) -> int:
        removed = 0
        for path in self.root.rglob("*.tmp-*"):
            if path.is_file() and self.root in path.parents:
                path.unlink()
                removed += 1
        return removed

    def status(self) -> dict[str, Any]:
        result = self.integrity()
        manifest = self._read_state("manifest.json", {})
        status = {
            "provider": "hermes_vault",
            "root": str(self.root),
            "database": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
            "sqlite_integrity": result.sqlite,
            "foreign_keys": list(result.foreign_keys),
            "integrity_ok": result.ok,
            "item_counts": self._counts(),
            "pending_writes": 0,
            "last_durable_turn": manifest.get("last_sync"),
            "last_checkpoint": manifest.get("last_checkpoint"),
            "last_backup": manifest.get("last_backup"),
            "permissions": {
                "root_mode": oct(self.root.stat().st_mode & 0o777),
                "database_mode": oct(self.db_path.stat().st_mode & 0o777),
            },
        }
        self._write_health(result)
        return status

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._write_health()
            self._conn.close()
            self._closed = True

    def __enter__(self) -> "VaultStore":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()
