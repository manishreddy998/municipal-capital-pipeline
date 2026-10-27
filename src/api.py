"""FastAPI service exposing source-cited infrastructure signals, timeline chains, and PDF receipts."""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.config import RESULTS
from src.warehouse import read_sql

app = FastAPI(
    title="Municipal Capital Signal API",
    version="1.0.0",
    description="Source-cited municipal infrastructure project signals with cross-document amendment chains and PDF receipt verification.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/signals")
def list_signals(
    municipality: str | None = Query(default=None),
    asset_category: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    if municipality:
        safe_m = municipality.replace("'", "''")
        clauses.append(f"lower(municipality_name) LIKE '%{safe_m.lower()}%'")
    if asset_category:
        safe_c = asset_category.replace("'", "''")
        clauses.append(f"lower(asset_category) = '{safe_c.lower()}'")
    where_sql = " AND ".join(clauses)
    q = f"""
        SELECT signal_id, project_code, municipality_name, region, agency_type,
               latest_project_title, asset_category, lifecycle_stage, resolution_status,
               initial_budget_cad, latest_verified_budget_cad, active_approved_budget_cad,
               cumulative_amendment_delta_cad, document_appearances, fiscal_years,
               funding_source, regulatory_driver, latest_doc_id, latest_file_name,
               latest_file_sha256, latest_page_number, latest_line_number,
               latest_verbatim_quote, receipt_verified
        FROM marts.mart_infrastructure_signals
        WHERE {where_sql}
        ORDER BY active_approved_budget_cad DESC, signal_id ASC
        LIMIT {int(limit)}
    """
    return read_sql(q).to_dict(orient="records")


@app.get("/timeline/{signal_id}")
def signal_timeline(signal_id: str) -> dict[str, Any]:
    safe_id = signal_id.replace("'", "''")
    df = read_sql(f"""
        SELECT appearance_seq, mention_id, doc_id, doc_type, meeting_date,
               project_code_stated, mention_project_name, lifecycle_stage,
               resolution_status, stated_total_cad, verified_sum_cad,
               civil_works_cad, mech_elec_cad, engineering_cad,
               table_math_gap_cad, cumulative_amendment_delta_cad,
               is_latest_record, is_superseded, file_name, file_sha256,
               page_number, line_number, verbatim_quote, receipt_verified
        FROM core.int_budget_amendment_chains
        WHERE resolved_entity_id = '{safe_id}'
        ORDER BY appearance_seq ASC
    """)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    return {
        "signal_id": signal_id,
        "appearances": len(df),
        "timeline": df.to_dict(orient="records"),
    }


@app.get("/scoreboard")
def get_scoreboard() -> dict[str, Any]:
    sb_path = RESULTS / "scoreboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="scoreboard.json not generated yet")
    return json.loads(sb_path.read_text(encoding="utf-8"))
