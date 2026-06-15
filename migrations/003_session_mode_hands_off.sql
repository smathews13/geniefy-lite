-- geniefy-v3 — Lakebase (Postgres) migration 003: add the 'hands_off' session_mode value.
-- Implements LLD-amend-005 §3 (U108 / D51): hands-off / schema-batch sessions persist
-- mode='hands_off' (generate + persist questions, never block/apply), so the geniefy.session_mode
-- enum (created in 001 as 'interactive'|'batch') must carry it. Additive + idempotent.
--
-- NB: ALTER TYPE ... ADD VALUE is supported inside a transaction on modern Postgres (PG12+, which
-- Lakebase is); we only ADD the label here (we do not USE it in the same txn), so it is safe under
-- the migration runner's per-file transaction. IF NOT EXISTS makes a re-run a no-op.

set search_path to geniefy;

alter type geniefy.session_mode add value if not exists 'hands_off';
