"""Command-line operations for the Hermes Vault store."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from .store import VaultError, VaultStore, utc_now


def _hermes_home(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    configured = os.environ.get("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser().resolve()
    except Exception:
        return (Path.home() / ".hermes").resolve()


def _store(args: argparse.Namespace) -> VaultStore:
    return VaultStore(_hermes_home(args.hermes_home) / "vault")


def _print(value: Any, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    else:
        print(value)


def _cmd_status(args: argparse.Namespace) -> int:
    with _store(args) as store:
        _print(store.status(), args.json)
    return 0


def _cmd_integrity(args: argparse.Namespace) -> int:
    with _store(args) as store:
        result = store.integrity()
        _print(result.as_dict(), args.json)
        store.record_event(
            "integrity_check",
            source_type="local_observation",
            source_ref="hermes-vault integrity",
            trust="high",
            content=f"integrity check: {result.sqlite}",
            metadata=result.as_dict(),
        )
    return 0 if result.ok else 1


def _cmd_backup(args: argparse.Namespace) -> int:
    home = _hermes_home(args.hermes_home)
    destination = (
        Path(args.destination).expanduser().resolve()
        if args.destination
        else home / "backups" / f"vault-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    with VaultStore(home / "vault") as store:
        path = store.backup_to(destination)
        _print({"backup": str(path), "browser_profile": "excluded"}, args.json)
    return 0


def _cmd_maintenance(args: argparse.Namespace) -> int:
    home = _hermes_home(args.hermes_home)
    with VaultStore(home / "vault") as store:
        result = store.integrity()
        if not result.ok:
            store._write_health(result)  # conservative: report, do not repair content
            _print({"ok": False, "integrity": result.as_dict()}, args.json)
            return 1
        removed = store.cleanup_temporary_files()
        backup = store.backup_to(
            home / "backups" / f"vault-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        backup_root = home / "backups"
        candidates = sorted(
            (path for path in backup_root.glob("vault-*") if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in candidates[max(1, int(args.keep)) :]:
            shutil.rmtree(stale)
        _print({"ok": True, "temporary_files_removed": removed, "backup": str(backup)}, args.json)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    with _store(args) as store:
        value = store.search(
            args.query,
            limit=args.limit,
            include_superseded=args.include_superseded,
            include_archived=args.include_archived,
            include_deleted=args.include_deleted,
            include_candidates=not args.exclude_candidates,
            min_trust=args.min_trust,
        )
        _print(value, True)
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    with _store(args) as store:
        value = store.get(
            args.memory_id,
            include_superseded=True,
            include_deleted=args.include_deleted,
        )
        if value is None:
            return 1
        _print(value, True)
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    with _store(args) as store:
        result = store.reindex()
        _print(result.as_dict(), True)
    return 0 if result.ok else 1


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    with _store(args) as store:
        value = store.checkpoint(
            args.name,
            [{"role": "user", "content": args.content}],
            session_id=args.session_id,
        )
        _print(value, True)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-vault")
    parser.add_argument("--hermes-home", help="active Hermes profile directory")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(function=_cmd_status)

    integrity = sub.add_parser("integrity")
    integrity.add_argument("--json", action="store_true")
    integrity.set_defaults(function=_cmd_integrity)

    backup = sub.add_parser("backup")
    backup.add_argument("--destination")
    backup.add_argument("--json", action="store_true")
    backup.set_defaults(function=_cmd_backup)

    maintenance = sub.add_parser("maintenance")
    maintenance.add_argument("--keep", type=int, default=7)
    maintenance.add_argument("--json", action="store_true")
    maintenance.set_defaults(function=_cmd_maintenance)

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--include-superseded", action="store_true")
    search.add_argument("--include-archived", action="store_true")
    search.add_argument("--include-deleted", action="store_true")
    search.add_argument("--exclude-candidates", action="store_true")
    search.add_argument("--min-trust", choices=["high", "medium", "low", "untrusted"])
    search.set_defaults(function=_cmd_search)

    get = sub.add_parser("get")
    get.add_argument("memory_id")
    get.add_argument("--include-deleted", action="store_true")
    get.set_defaults(function=_cmd_get)

    reindex = sub.add_parser("reindex")
    reindex.set_defaults(function=_cmd_reindex)

    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("name")
    checkpoint.add_argument("content")
    checkpoint.add_argument("--session-id", default="")
    checkpoint.set_defaults(function=_cmd_checkpoint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (VaultError, OSError, ValueError) as exc:
        print(f"hermes-vault: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
