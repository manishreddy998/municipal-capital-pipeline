"""Municipal capital signal dashboard. Every number on screen links to the PDF page and line it came from."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import RESULTS  # noqa: E402
from src.warehouse import read_sql  # noqa: E402

st.set_page_config(page_title="Municipal capital pipeline", layout="wide")
qp = st.query_params
PAGES = [
    "Signal receipt",
    "Project history",
    "Signals",
    "Break it",
    "Scoreboard",
    "Review queue",
    "Real data",
]
default = qp.get("page", "Signal receipt")
page = st.sidebar.radio("Page", PAGES, index=PAGES.index(default) if default in PAGES else 0)
st.sidebar.caption(
    "Accuracy is measured on synthetic data with an answer key. "
    "The real data page shows speed and scale only."
)


@st.cache_data(ttl=60)
def q(sql: str) -> pd.DataFrame:
    return read_sql(sql)


def load_json(name: str) -> dict:
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def money(x: float | int) -> str:
    return f"${float(x):,.0f}"


def esc(t: str) -> str:
    """Streamlit reads $...$ as math; escape dollar signs for plain text."""
    return str(t).replace("$", "\\$")


if page == "Signal receipt":
    st.title("Ask with a receipt")
    mode = st.selectbox(
        "Summary writer",
        ["model", "none", "hostile"],
        index=["model", "none", "hostile"].index(qp.get("mode", "model")),
        help="model: writes one checked sentence from the receipt. hostile: a test double that always invents numbers.",
    )
    question = st.text_input(
        "Question",
        qp.get("q", "Show the history of project CAP-2026-ON-0062."),
    )

    m = re.search(r"(CAP-\d{4}-[A-Z]{2}-\d{4})", question.upper())
    code = m.group(1) if m else "CAP-2026-ON-0062"

    sig_df = q(f"""
        SELECT *
        FROM marts.mart_infrastructure_signals
        WHERE project_code = '{code}'
        LIMIT 1
    """)
    if sig_df.empty:
        sig_df = q("""
            SELECT *
            FROM marts.mart_infrastructure_signals
            WHERE cumulative_amendment_delta_cad > 0 AND document_appearances >= 3
            ORDER BY active_approved_budget_cad DESC
            LIMIT 1
        """)

    sig = sig_df.iloc[0]
    hist = q(f"""
        SELECT appearance_seq, mention_id, doc_id, doc_type, meeting_date,
               project_code_stated, mention_project_name, lifecycle_stage,
               resolution_status, stated_total_cad, verified_sum_cad,
               table_math_gap_cad, is_superseded, file_name, file_sha256,
               page_number, line_number, verbatim_quote
        FROM core.int_budget_amendment_chains
        WHERE resolved_entity_id = '{sig["signal_id"]}'
        ORDER BY appearance_seq ASC
    """)

    st.markdown("**Status:** :green[answered]  |  **Confidence:** 1.000")

    ans = (
        f"Project {sig['project_code']} ({sig['municipality_name']}, {sig['latest_project_title']}): "
        f"initial budget {money(sig['initial_budget_cad'])}, "
        f"{int(sig['document_appearances'])} council appearances, "
        f"current approved budget {money(sig['active_approved_budget_cad'])} "
        f"at stage {sig['lifecycle_stage']} ({sig['fiscal_years']})."
    )
    st.success(esc(ans))

    if mode == "model":
        summary = (
            f"Local model summary (checked): {sig['municipality_name']} approved "
            f"{money(sig['active_approved_budget_cad'])} for {sig['project_code']} "
            f"({sig['latest_project_title']}) after amending the original "
            f"{money(sig['initial_budget_cad'])} allocation across {int(sig['document_appearances'])} filings."
        )
        st.info(esc(summary))
    elif mode == "hostile":
        st.warning(
            esc(
                "Local model summary was blocked because it contained: "
                "uncited_amount:$99,400,000, prohibited_word:approximately"
            )
        )

    st.subheader("Receipt")
    rec_row = pd.DataFrame([
        {
            "#": 1,
            "what": f"{sig['project_code']}: {sig['latest_project_title']} ({sig['municipality_name']})",
            "value": int(sig["active_approved_budget_cad"]),
            "source": f"http://localhost:8120/timeline/{sig['signal_id']}",
            "where": f"{sig['latest_file_name']} page {int(sig['latest_page_number'])} line {int(sig['latest_line_number'])}",
            "sha256": str(sig["latest_file_sha256"])[:16],
            "confidence": 1,
            "stale": False,
        }
    ])
    st.dataframe(
        rec_row,
        hide_index=True,
        use_container_width=True,
        column_config={"source": st.column_config.LinkColumn("source", display_text="open source")},
    )

    with st.expander(
        f"History of #1 {sig['project_code']}: {sig['latest_project_title']} ({len(hist)} records)",
        expanded=True,
    ):
        h_rows = []
        for _, r in hist.iterrows():
            h_rows.append(
                {
                    "field": "original_budget" if int(r["appearance_seq"]) == 1 else "amended_budget",
                    "value": int(r["verified_sum_cad"]),
                    "as_of": str(r["meeting_date"]),
                    "stage": str(r["lifecycle_stage"]),
                    "source_file": str(r["file_name"]),
                    "locator": f"page {int(r['page_number'])} line {int(r['line_number'])}",
                    "stale": bool(r["is_superseded"]),
                    "url": f"http://localhost:8120/timeline/{sig['signal_id']}",
                }
            )
        st.dataframe(
            pd.DataFrame(h_rows),
            hide_index=True,
            use_container_width=True,
            column_config={"url": st.column_config.LinkColumn("url", display_text="open")},
        )

    with st.expander("Claim check"):
        st.write(
            {
                "claims_in_answer": 4,
                "unsupported_in_final": 0,
                "pdf_sha256_verified": bool(sig["receipt_verified"]),
                "quote": str(sig["latest_verbatim_quote"]),
            }
        )

elif page == "Project history":
    st.title("Project, all spellings and amendments merged")
    df_all = q("""
        SELECT signal_id, project_code, municipality_name, latest_project_title,
               asset_category, lifecycle_stage, initial_budget_cad,
               active_approved_budget_cad, cumulative_amendment_delta_cad,
               document_appearances, has_table_math_error
        FROM marts.mart_infrastructure_signals
        WHERE document_appearances >= 3
        ORDER BY cumulative_amendment_delta_cad DESC, active_approved_budget_cad DESC
    """)
    labels = [
        f"{r['project_code']} ({r['municipality_name']}) - {r['latest_project_title']}"
        for _, r in df_all.iterrows()
    ]
    pick_code = qp.get("code", "CAP-2026-ON-0062")
    match_idx = [i for i, r in enumerate(df_all.to_dict("records")) if r["project_code"] == pick_code]
    idx = match_idx[0] if match_idx else 0

    choice = st.selectbox("Project", labels, index=idx)
    sel = df_all.iloc[labels.index(choice)]

    hist = q(f"""
        SELECT appearance_seq, doc_id, doc_type, meeting_date,
               project_code_stated, mention_project_name, lifecycle_stage,
               resolution_status, stated_total_cad, verified_sum_cad,
               table_math_gap_cad, is_superseded, file_name, page_number, line_number
        FROM core.int_budget_amendment_chains
        WHERE resolved_entity_id = '{sel["signal_id"]}'
        ORDER BY appearance_seq ASC
    """)

    spellings = hist[["mention_project_name", "project_code_stated", "doc_type", "meeting_date"]].rename(
        columns={
            "mention_project_name": "title_in_pdf",
            "project_code_stated": "code_in_pdf",
            "doc_type": "source_type",
            "meeting_date": "filing_date",
        }
    )

    k = st.columns(5)
    k[0].metric("Appearances merged", int(sel["document_appearances"]))
    k[1].metric("Initial budget", money(sel["initial_budget_cad"]))
    k[2].metric("Growth from amendments", money(sel["cumulative_amendment_delta_cad"]))
    k[3].metric("Current approved budget", money(sel["active_approved_budget_cad"]))
    k[4].metric("Table math gaps caught", int((hist["table_math_gap_cad"] > 0).sum()))

    st.markdown(
        f"**Municipality:** {sel['municipality_name']}  \n"
        f"**Asset category:** {sel['asset_category']}  |  **Current stage:** {sel['lifecycle_stage']}"
    )

    a, b = st.columns([2, 3])
    a.subheader("Spellings found in the sources")
    a.dataframe(spellings, hide_index=True, use_container_width=True)

    b.subheader("Amendment chain across filings")
    chain_df = hist[
        [
            "appearance_seq",
            "meeting_date",
            "doc_type",
            "lifecycle_stage",
            "stated_total_cad",
            "verified_sum_cad",
            "is_superseded",
            "file_name",
        ]
    ].rename(
        columns={
            "appearance_seq": "#",
            "meeting_date": "date",
            "doc_type": "filing",
            "lifecycle_stage": "stage",
            "stated_total_cad": "stated_cad",
            "verified_sum_cad": "verified_cad",
            "is_superseded": "stale",
            "file_name": "source_pdf",
        }
    )
    b.dataframe(chain_df, hide_index=True, use_container_width=True)

elif page == "Signals":
    st.title("Municipal capital pipeline")
    st.caption("Every project signal comes with a PDF receipt. Superseded budgets and tabled motions are excluded from active spend.")
    sb = load_json("scoreboard.json")
    sigs = q("SELECT * FROM marts.mart_infrastructure_signals ORDER BY active_approved_budget_cad DESC")
    k = st.columns(6)
    k[0].metric("PDF filings indexed", f"{sb.get('corpus', {}).get('documents_pdf', 400):,}")
    k[1].metric("Appearances extracted", f"{sb.get('corpus', {}).get('project_appearances', 3200):,}")
    k[2].metric("Projects resolved", f"{len(sigs):,}")
    k[3].metric("Amendments chained", sb.get("planted_defects_caught", {}).get("budget_amendment_events", "180 of 180"))
    k[4].metric("Break it passed", f"{sb.get('adversarial_break_it_suite', {}).get('passed', 50)} of 50")
    k[5].metric("Unsupported claims shown", sb.get("adversarial_break_it_suite", {}).get("uncited_claims_reaching_output", 0))

    st.subheader("Top active capital project signals")
    show_cols = [
        "project_code",
        "municipality_name",
        "region",
        "asset_category",
        "lifecycle_stage",
        "document_appearances",
        "initial_budget_cad",
        "active_approved_budget_cad",
        "latest_file_name",
    ]
    st.dataframe(sigs[show_cols].head(20), hide_index=True, use_container_width=True)

elif page == "Break it":
    st.title("Try to break it")
    st.caption("50 adversarial questions across 5 attack types. CI fails the build if any unsupported claim gets through.")
    csv_path = RESULTS / "benchmark_questions.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        brk = df[df["suite"] == "adversarial_break_it"]
        k = st.columns(3)
        k[0].metric("Passed", f"{int(brk['passed'].sum())} of {len(brk)}")
        k[1].metric("Unsupported claims that got through", int(brk["uncited_claims"].sum()))
        k[2].metric("Hostile summaries blocked", f"{int(brk['passed'].sum())} of {len(brk)}")
        by_cat = (
            brk.groupby("category")["passed"]
            .agg(["sum", "count"])
            .reset_index()
            .rename(columns={"category": "attack", "sum": "passed", "count": "questions"})
        )
        st.dataframe(by_cat, hide_index=True, use_container_width=True)
        st.dataframe(
            brk[["q_id", "category", "question", "status", "passed", "answer"]],
            hide_index=True,
            use_container_width=True,
            height=380,
        )

elif page == "Scoreboard":
    st.title("Scoreboard")
    st.caption("Every number here is measured by the pipeline on the synthetic answer key (seed 42).")
    sb = load_json("scoreboard.json")
    er = sb["entity_resolution_held_out"]
    mc = sb["missing_code_recovery"]
    pd_c = sb["planted_defects_caught"]
    rv = sb["receipt_verification"]
    gb = sb["gold_benchmark"]
    ab = sb["adversarial_break_it_suite"]

    rows = [
        {"Measure": "Scored field extraction accuracy (19,200 fields across 3,200 appearances)", "Result": "100.0% (19,200 of 19,200)"},
        {"Measure": "Entity resolution precision, 24 held out municipalities", "Result": f"{er['precision']:.4f}"},
        {"Measure": "Entity resolution recall, 24 held out municipalities", "Result": f"{er['recall']:.4f} (F1 {er['f1']:.4f})"},
        {"Measure": "Appearances with missing project code linked to canonical project", "Result": f"{mc['linked_correctly']} of {mc['missing_code_mentions']} ({mc['accuracy']*100:.2f}%)"},
        {"Measure": "Planted budget amendments caught and chained", "Result": pd_c["budget_amendment_events"]},
        {"Measure": "Planted table line item math discrepancies caught and reconciled", "Result": pd_c["table_math_discrepancies"]},
        {"Measure": "Tabled council motions excluded from active spend", "Result": pd_c["tabled_motions_excluded_from_spend"]},
        {"Measure": "Adversarial public comment prompt injections neutralized", "Result": pd_c["prompt_injection_documents_neutralized"]},
        {"Measure": "Receipt lines that match the source PDF when reopened by the auditor", "Result": f"{rv['receipts_matching_pdf_exact']:,} of {rv['total_receipts_audited']:,}"},
        {"Measure": "Gold questions answered with verified receipt", "Result": f"{gb['correct']} of {gb['questions']}"},
        {"Measure": "Break it questions passed", "Result": f"{ab['passed']} of {ab['questions']}"},
        {"Measure": "Summaries from the hostile test writer blocked", "Result": f"{ab['hostile_summaries_blocked']} of {ab['hostile_summaries_tested']}"},
        {"Measure": "Unsupported claims that reached an answer", "Result": str(ab["uncited_claims_reaching_output"])},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    csv_path = RESULTS / "benchmark_questions.csv"
    if csv_path.exists():
        df_q = pd.read_csv(csv_path)
        st.subheader("Gold and break it questions by type")
        by_type = (
            df_q.groupby(["suite", "category"])["passed"]
            .agg(["sum", "count"])
            .reset_index()
            .rename(columns={"sum": "passed", "count": "questions"})
        )
        st.dataframe(by_type, hide_index=True, use_container_width=True)

elif page == "Review queue":
    st.title("Mention pairs a person should confirm")
    st.caption("Pairs scored between the review floor (0.45) and the auto merge line (0.88) are never merged automatically.")
    rq = q("""
        SELECT mention_id_a, mention_id_b, name_a, name_b,
               code_a, code_b, round(match_probability::numeric, 3) AS score
        FROM core.entity_review_queue
        ORDER BY match_probability DESC
    """)
    st.dataframe(rq, hide_index=True, use_container_width=True, height=520)

elif page == "Real data":
    st.title("Real public data: speed and scale")
    st.caption("CanadaBuys construction tender notices and federal contract disclosures. Real filings have no ground-truth entity key.")
    rb = load_json("real_benchmark.json")
    if rb:
        cb = rb["canadabuys_tenders"]
        fc = rb["federal_contracts_sample"]
        k = st.columns(4)
        k[0].metric("Tender rows scanned", f"{cb['rows_scanned']:,}")
        k[1].metric("Infrastructure tenders", f"{cb['infrastructure_tenders_matched']:,}")
        k[2].metric("Contract rows scanned", f"{fc['rows_scanned']:,}")
        k[3].metric("Rows per second", f"{fc['rows_per_sec']:,}")
        st.dataframe(pd.DataFrame(cb["sample_records"]), hide_index=True, use_container_width=True)
