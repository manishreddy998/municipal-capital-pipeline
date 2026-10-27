"""Dual-target warehouse adapter supporting local DuckDB and Google BigQuery."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import duckdb
import pandas as pd

from src.config import ROOT, duckdb_path


def get_target() -> str:
    if os.environ.get("GCP_PROJECT_ID") and os.environ.get("WAREHOUSE_TARGET", "").lower() == "bigquery":
        return "bigquery"
    return "duckdb"


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    db_file = duckdb_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_file))
    for schema in ("raw", "staging", "core", "marts"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    return con


def write_table(df: pd.DataFrame, table: str, schema: str = "raw") -> int:
    target = get_target()
    if target == "bigquery":
        from google.cloud import bigquery  # type: ignore

        project_id = os.environ["GCP_PROJECT_ID"]
        dataset = os.environ.get(f"BQ_DATASET_{schema.upper()}", f"{schema}_municipal")
        client = bigquery.Client(project=project_id)
        table_id = f"{project_id}.{dataset}.{table}"
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
        return len(df)

    con = connect_duckdb()
    try:
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {schema}.{table} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
        return len(df)
    finally:
        con.close()


def read_sql(query: str) -> pd.DataFrame:
    target = get_target()
    if target == "bigquery":
        from google.cloud import bigquery  # type: ignore

        client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"])
        return client.query(query).to_dataframe()

    con = connect_duckdb()
    try:
        return con.execute(query).df()
    finally:
        con.close()


def run_dbt(select: str) -> dict:
    t0 = time.time()
    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(ROOT / "dbt")
    env["DUCKDB_PATH"] = str(duckdb_path())
    dbt_bin = os.environ.get("DBT_BIN", "dbt")
    if not Path(dbt_bin).exists():
        local_dbt = Path.home() / ".local" / "bin" / "dbt"
        if local_dbt.exists():
            dbt_bin = str(local_dbt)

    cmd = [dbt_bin, "--no-use-colors", "build", "--select", select, "--project-dir", str(ROOT / "dbt")]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"dbt build --select {select} failed:\n{output[-4000:]}")
    pass_lines = [line.strip() for line in output.splitlines() if "PASS=" in line or "Done." in line]
    return {
        "select": select,
        "seconds": round(time.time() - t0, 2),
        "summary": pass_lines[-1] if pass_lines else "completed",
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="staging", choices=["staging", "marts", "all"])
    args = parser.parse_args()
    if args.step == "all":
        res = [run_dbt("tag:staging"), run_dbt("tag:marts")]
    else:
        res = run_dbt(f"tag:{args.step}")
    print(json.dumps(res, indent=2))
