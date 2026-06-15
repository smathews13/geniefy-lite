#!/usr/bin/env python3
"""geniefy-v3 — Lakebase migration runner (U62, NFR-D / D35).

Applies ``migrations/*.sql`` to the Lakebase ``geniefy`` database, **idempotently**.
Codifies the path validated live on 2026-06-12 (see ``APPLY.md``): psycopg2's simple-query
protocol runs the whole multi-statement file (``DO $$…$$`` blocks, function bodies, seeds)
as one unit, and ``CREATE DATABASE`` runs on a separate **autocommit** connection.

Invoked two ways:
  - locally by ``deploy.sh`` step 5 (the validated path — CLI + token on the deploy host), and
  - on a Databricks job cluster by the ``geniefy_setup`` job (``databricks bundle run geniefy_setup``).

Connection coordinates come from env (set by the caller); any not provided are derived from
the ``databricks`` CLI (the exact APPLY.md commands):
  GENIEFY_PG_HOST · GENIEFY_PG_DATABASE (default ``geniefy``) · GENIEFY_PG_USER · GENIEFY_PG_TOKEN
  GENIEFY_LAKEBASE_PROJECT (default ``projects/geniefy``) · GENIEFY_LAKEBASE_BRANCH/ENDPOINT
  DATABRICKS_CONFIG_PROFILE
Re-running is safe: the SQL is guarded (``if not exists`` / guarded enum creation /
``on conflict do nothing``) and ``CREATE DATABASE`` tolerates an existing database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent
DB = os.environ.get("GENIEFY_PG_DATABASE", "geniefy")
PROJECT = os.environ.get("GENIEFY_LAKEBASE_PROJECT", "projects/geniefy")
# Branch: argv[1] (the geniefy_setup job's `lakebase_branch` parameter — U118) wins over the env;
# the local deploy.sh path sets GENIEFY_LAKEBASE_BRANCH per-target (U117). Default production.
BRANCH = ((sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else "")
          or os.environ.get("GENIEFY_LAKEBASE_BRANCH") or "production")
ENDPOINT = os.environ.get("GENIEFY_LAKEBASE_ENDPOINT", "primary")
PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE")


def _cli(*args: str) -> dict:
    """Run a `databricks ... -o json` command (honoring the profile) and parse the output."""
    cmd = ["databricks", *args] + (["-p", PROFILE] if PROFILE else []) + ["-o", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"[migrate] CLI failed: {' '.join(cmd)}\n{out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def _coords() -> tuple[str, str, str]:
    """(host, user, short-lived token). Prefer env; derive the rest from the CLI (APPLY.md)."""
    host = os.environ.get("GENIEFY_PG_HOST")
    user = os.environ.get("GENIEFY_PG_USER")
    token = os.environ.get("GENIEFY_PG_TOKEN")
    if host and user and token:
        return host, user, token
    ep = f"{PROJECT}/branches/{BRANCH}/endpoints/{ENDPOINT}"
    if not host:
        host = _cli("postgres", "list-endpoints", f"{PROJECT}/branches/{BRANCH}")[0]["status"]["hosts"]["host"]
    if not user:
        user = _cli("current-user", "me")["userName"]
    if not token:  # OAuth db credential — short-lived, generated immediately before use
        token = _cli("postgres", "generate-database-credential", ep)["token"]
    return host, user, token


def _connect(host: str, user: str, token: str, dbname: str):
    import psycopg2  # required only at run time (on the deploy host / job env), not to import

    return psycopg2.connect(host=host, port=5432, dbname=dbname, user=user,
                            password=token, sslmode="require")


def main() -> int:
    host, user, token = _coords()
    print(f"[migrate] Lakebase host={host} db={DB} user={user}")

    # 1. ensure the working-store database exists (CREATE DATABASE cannot run in a txn → autocommit)
    import psycopg2

    conn = _connect(host, user, token, "postgres")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(f'CREATE DATABASE "{DB}"')
                print(f"[migrate] database {DB!r}: created")
            except psycopg2.errors.DuplicateDatabase:
                print(f"[migrate] database {DB!r}: already exists (ok)")
    finally:
        conn.close()

    # 2. apply each migration in order (idempotent; whole file per simple-query execute, then commit)
    sql_files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    if not sql_files:
        raise SystemExit(f"[migrate] no migration .sql files found in {MIGRATIONS_DIR}")
    conn = _connect(host, user, token, DB)
    try:
        for f in sql_files:
            with conn.cursor() as cur:
                cur.execute(f.read_text())
            conn.commit()
            print(f"[migrate] applied {f.name}: OK")
    finally:
        conn.close()

    print(f"[migrate] done — {len(sql_files)} migration(s) applied to {DB}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
