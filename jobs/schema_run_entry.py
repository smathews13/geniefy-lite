#!/usr/bin/env python3
"""geniefy-v3 — hands-off schema-run Job entrypoint (D54 / U119).

Entrypoint of the standalone ``geniefy_schema_run`` Job, which lives in its OWN bundle
(``jobs-bundle/``) deployed independently of the app so its ``bundle deploy`` never reconciles the
app's resources (grant-safe). It builds the SP-backed ``SessionService`` on the cluster and drives
the batch generation (``run_schema``). A **no-op** when no ``schema_run_id`` is passed. Migrations
(003/004) are applied separately by ``deploy.sh`` / the ``geniefy_setup`` job, not here.

argv (the task ``parameters``, fed by job parameters — see jobs-bundle/databricks.yml):
  schema_run_id catalog schema filters_json model_endpoint warehouse_id pg_host pg_database lakebase_endpoint

Integration code (validated live at U113, not in the hermetic suite); the batch LOGIC it calls lives
in ``geniefy_app.schema_run.run_schema`` and is unit-tested with fakes.
"""
import json
import os
import sys


def _ensure_geniefy_importable() -> None:
    """Make ``geniefy_app`` importable on the Job cluster WITHOUT relying on ``__file__`` — a
    Databricks ``spark_python_task`` runs the file via ``exec()``, so ``__file__`` is undefined
    (U122/U113). The synced files dir is usually already on ``sys.path`` (direct import works);
    otherwise hunt candidate roots (cwd, argv[0]'s dir, and ``__file__`` only if it exists), trying
    both the geniefy-jobs layout (``geniefy_app`` beside the entrypoint) and the app-bundle layout
    (``app/geniefy_app``)."""
    try:
        import geniefy_app  # noqa: F401 — files dir already on sys.path
        return
    except ModuleNotFoundError:
        pass
    bases: list[str] = [os.getcwd()]
    if sys.argv and sys.argv[0]:
        bases.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    try:
        bases.append(os.path.dirname(os.path.abspath(__file__)))  # only if defined
    except NameError:
        pass
    seen: set[str] = set()
    for base in bases:
        for cand in (base, os.path.join(base, "app"), os.path.join(base, ".."),
                     os.path.join(base, "..", "app")):
            ap = os.path.abspath(cand)
            if ap in seen:
                continue
            seen.add(ap)
            if os.path.isdir(os.path.join(ap, "geniefy_app")):
                sys.path.insert(0, ap)
                return
    raise ModuleNotFoundError("geniefy_app not found on the Job cluster (checked cwd/argv/file dirs)")


def main(argv: list[str]) -> int:
    args = (list(argv) + [""] * 9)[:9]
    (schema_run_id, catalog, schema, filters_json,
     model_endpoint, warehouse_id, pg_host, pg_database, lakebase_endpoint) = args

    if not schema_run_id:
        print("[schema_run] no schema_run_id — migrate-only run, nothing to do.")
        return 0

    # Config from job params → env → AppConfig.from_env (so all tunable defaults apply). Pin the
    # column token cap to 20000 (U94) so wide-table column batches don't truncate the JSON output.
    if model_endpoint:
        os.environ["GENIEFY_MODEL_ENDPOINT"] = model_endpoint
    if warehouse_id:
        os.environ["GENIEFY_WAREHOUSE_ID"] = warehouse_id
    if pg_host:
        os.environ["GENIEFY_PG_HOST"] = pg_host
    if lakebase_endpoint:
        os.environ["GENIEFY_LAKEBASE_ENDPOINT"] = lakebase_endpoint  # for cluster cred minting (U123)
    os.environ.setdefault("GENIEFY_PG_DATABASE", pg_database or "geniefy")
    os.environ.setdefault("GENIEFY_REASON_COLUMN_MAX_TOKENS", "20000")

    _ensure_geniefy_importable()
    from geniefy_app.api import build_service
    from geniefy_app.config import AppConfig
    from geniefy_app.schema_run import run_schema

    service = build_service(AppConfig.from_env())
    filters = json.loads(filters_json) if filters_json else {}
    summary = run_schema(service, service.store, schema_run_id=schema_run_id,
                         catalog=catalog, schema=schema, filters=filters)
    print(f"[schema_run] {schema_run_id} complete: {summary}")
    return 0


if __name__ == "__main__":
    # Do NOT sys.exit(0) on success: a spark_python_task runs this file via exec(), where a raised
    # SystemExit(0) is treated as a task error (→ spurious retry, U123 live finding). Return normally
    # on success; raise (non-zero) only on genuine failure.
    _rc = main(sys.argv[1:])
    if _rc:
        sys.exit(_rc)
