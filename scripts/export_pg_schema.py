"""重新生成 Go 侧的 PostgreSQL schema 基线（tasks.md D-7 交付物）。

产物：`go-core/db/schema/0001_legacy_auth.sql` —— Go 工具链（N2 的 sqlc）编译时用的
只读快照。**DDL 属主仍是 backend/alembic**，本脚本只导出、不参与迁移。

用法：
    .venv/bin/python scripts/export_pg_schema.py --docker closenai-pg-dev
    .venv/bin/python scripts/export_pg_schema.py --docker closenai-pg-dev --check
    .venv/bin/python scripts/export_pg_schema.py            # 本机装有 pg_dump 时

连接串默认取 settings.database_url（SQLAlchemy 的 postgresql+asyncpg:// 会译成
libpq 的 postgresql://）。pg_dump 16.9+ 会在开头插入一次性 `\restrict <随机 token>`，
它每次导出都不同，因此脚本会剔除这类行，保证快照可复现。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "go-core" / "db" / "schema" / "0001_legacy_auth.sql"
DEFAULT_TABLES = ("public.users", "public.refresh_tokens")
BODY_MARKER = "--\n-- PostgreSQL database dump"
PSQL_META_RE = re.compile(r"^\\(restrict|unrestrict)\b")


def dump(docker: str | None, database_url: str, tables: tuple[str, str, ...]) -> str:
    args = ["pg_dump", "-U", "postgres", "--schema-only", "--no-owner", "--no-privileges"]
    for table in tables:
        args += ["-t", table]
    if docker:
        # 容器内不需要连接串：本地 socket 即为该库所在主机。
        proc = subprocess.run(["docker", "exec", docker, *args, "-d", "closenai"],
                              capture_output=True, text=True)
    else:
        proc = subprocess.run([*args, database_url], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump 失败: {proc.stderr.strip()[:400]}")
    return proc.stdout


def alembic_head(docker: str | None, database_url: str) -> str:
    args = ["psql", "-U", "postgres", "-d", "closenai", "-Atc",
            "select version_num from public.alembic_version"]
    cmd = ["docker", "exec", docker, *args] if docker else [*args, database_url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def strip_psql_meta(raw: str) -> str:
    body = raw[raw.index(BODY_MARKER):]
    kept = [line for line in body.splitlines() if not PSQL_META_RE.match(line)]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n"


def header(docker: str | None, database_url: str, tables: tuple[str, str, ...],
           captured: str) -> str:
    command = (
        f"docker exec {docker} pg_dump -U postgres -d closenai --schema-only "
        if docker else f"pg_dump --schema-only {database_url} "
    )
    command += "--no-owner --no-privileges " + " ".join(f"-t {t}" for t in tables)
    return f"""-- Canonical schema baseline handed to the Go toolchain (D-7).
-- Provenance: pg_dump of the PostgreSQL 16 database produced by `alembic upgrade head`
--   command: {command}
--   captured: {captured}; alembic head revision {alembic_head(docker, database_url)}
-- DDL ownership stays with backend/alembic until N2 defines the canonical tables;
-- this file is the read-only snapshot sqlc/golang tooling generates against.
-- Regenerate with scripts/export_pg_schema.py; do not hand-edit.

"""


def render(docker: str | None, database_url: str, tables: tuple[str, ...],
           captured: str) -> tuple[str, str]:
    body = strip_psql_meta(dump(docker, database_url, tables))
    return header(docker, database_url, tables, captured), body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", help="在指定容器内执行 pg_dump/psql")
    parser.add_argument("--database-url", default=None,
                        help="缺省读 settings.database_url")
    parser.add_argument("--table", action="append", dest="tables",
                        help="可重复；缺省 users 与 refresh_tokens")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true",
                        help="只比对 DDL 主体（表头日期/revision 不参与比较）")
    args = parser.parse_args()

    database_url = args.database_url or _settings_url()
    tables = tuple(args.tables) if args.tables else DEFAULT_TABLES
    new_header, new_body = render(args.docker, database_url, tables,
                                  date.today().isoformat())

    if args.check:
        if not args.out.exists():
            raise SystemExit(f"MISSING {args.out}")
        current = args.out.read_text(encoding="utf-8")
        if current[current.index(BODY_MARKER):] != new_body:
            raise SystemExit(f"DRIFT {args.out}: rerun scripts/export_pg_schema.py")
        print(f"{args.out}: DDL 主体一致（{len(new_body.splitlines())} 行）")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(new_header + new_body, encoding="utf-8")
    print(f"{args.out}: {len(new_body.splitlines())} 行, tables={','.join(tables)}")


def _settings_url() -> str:
    sys.path.insert(0, str(ROOT))
    from backend.config import settings

    url = settings.database_url
    return url.replace("postgresql+asyncpg://", "postgresql://")


if __name__ == "__main__":
    main()
