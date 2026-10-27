"""Cloud Composer (Apache Airflow) DAG for the municipal capital signal pipeline.

Orchestrates PDF ingestion via Apache Beam / Dataflow, structured extraction on
Vertex AI Gemini with sha256 content caching, dbt staging transformations,
cross-document entity resolution, dbt marts, and the zero-uncited-claim release gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
except ImportError:
    # Lightweight shim so the DAG definition can be imported and verified in CI without full Airflow installed
    class DAG:  # type: ignore
        def __init__(self, dag_id: str, **kwargs):
            self.dag_id = dag_id
            self.kwargs = kwargs
            self.tasks = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class PythonOperator:  # type: ignore
        def __init__(self, task_id: str, python_callable, dag: DAG | None = None, **kwargs):
            self.task_id = task_id
            self.python_callable = python_callable
            self.downstream: list[str] = []
            if dag is not None:
                dag.tasks.append(self)

        def __rshift__(self, other):
            self.downstream.append(other.task_id)
            return other


from src.beam_ingest import run_beam_ingest
from src.warehouse import run_dbt


def _step_extract():
    from src.extractor import run_extraction
    return run_extraction()


def _step_resolve():
    from src.entity_resolution import run_entity_resolution
    return run_entity_resolution()


def _step_eval_gate():
    from src.evaluate import run_full_evaluation
    res = run_full_evaluation()
    if not res["gate_passed"]:
        raise RuntimeError(f"Release gate failed: {res}")
    return res


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "Email_on_failure": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="municipal_capital_signal_pipeline",
    default_args=default_args,
    description="Ingest municipal council and CIP PDFs, extract cited project signals, link entities, and gate on accuracy",
    schedule_interval="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["municipal", "capital-projects", "bigquery", "vertex-ai", "dbt"],
) as dag:
    ingest_pdfs = PythonOperator(
        task_id="beam_ingest_municipal_pdfs",
        python_callable=run_beam_ingest,
        dag=dag,
    )

    extract_signals = PythonOperator(
        task_id="extract_structured_signals",
        python_callable=_step_extract,
        dag=dag,
    )

    dbt_staging = PythonOperator(
        task_id="dbt_build_staging",
        python_callable=lambda: run_dbt("tag:staging"),
        dag=dag,
    )

    resolve_projects = PythonOperator(
        task_id="resolve_cross_document_projects",
        python_callable=_step_resolve,
        dag=dag,
    )

    dbt_marts = PythonOperator(
        task_id="dbt_build_marts",
        python_callable=lambda: run_dbt("tag:marts"),
        dag=dag,
    )

    evaluation_gate = PythonOperator(
        task_id="run_claim_and_accuracy_gate",
        python_callable=_step_eval_gate,
        dag=dag,
    )

    ingest_pdfs >> extract_signals >> dbt_staging >> resolve_projects >> dbt_marts >> evaluation_gate
