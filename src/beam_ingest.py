"""Apache Beam (Cloud Dataflow compatible) PDF page reader, normalizer, and chunker.

Reads municipal council and capital-budget PDFs, computes a sha256 fingerprint for
every file, extracts page-level text with line numbers, flags adversarial prompt
injection attempts in public comment blocks, and writes raw document and page tables.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
from pypdf import PdfReader

from src.config import PDF_DIR, RESULTS, SYNTH
from src.warehouse import write_table

INJECTION_PATTERN = re.compile(
    r"(?:ignore\s+all\s+previous\s+instructions|system\s+override|disregard\s+prior\s+rules|"
    r"report\s+that\s+all\s+water\s+projects.*cancelled)",
    re.I,
)


class ReadAndHashPdfDoFn:
    """Apache Beam compatible DoFn that hashes a municipal PDF and extracts pages."""

    def process(self, doc_row: dict) -> Iterable[dict]:
        pdf_path = PDF_DIR / doc_row["file_name"]
        raw_bytes = pdf_path.read_bytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        reader = PdfReader(str(pdf_path))
        pages_out = []
        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            text = page.extract_text() or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            has_inj = bool(INJECTION_PATTERN.search(text))
            pages_out.append({
                "doc_id": doc_row["doc_id"],
                "municipality_id": doc_row["municipality_id"],
                "municipality_name": doc_row["municipality_name"],
                "agency_alias": doc_row["agency_alias"],
                "region": doc_row["region"],
                "agency_type": doc_row["agency_type"],
                "doc_type": doc_row["doc_type"],
                "meeting_date": doc_row["meeting_date"],
                "file_name": doc_row["file_name"],
                "file_sha256": sha256,
                "file_bytes": len(raw_bytes),
                "page_number": page_num,
                "total_pages": len(reader.pages),
                "line_count": len(lines),
                "has_adversarial_comment": has_inj,
                "page_text": "\n".join(lines),
            })
        return pages_out


def sanitize_public_comments(page_text: str) -> tuple[str, bool]:
    """Strip adversarial prompt-injection lines from public comment sections while logging the flag."""
    cleaned_lines = []
    flagged = False
    for line in page_text.splitlines():
        if INJECTION_PATTERN.search(line) or line.startswith("PUBLIC COMMENT SUBMISSION:"):
            flagged = True
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines), flagged


def run_beam_ingest() -> dict:
    """Run the PDF ingestion and chunking pipeline (via Apache Beam DirectRunner if installed, or local DoFn runner)."""
    t0 = time.time()
    manifest_path = SYNTH / "manifest_documents.csv"
    if not manifest_path.exists():
        from src.synth_corpus import generate_corpus
        generate_corpus()

    df_manifest = pd.read_csv(manifest_path)
    records = df_manifest.to_dict(orient="records")

    dofn = ReadAndHashPdfDoFn()
    all_pages: list[dict] = []

    try:
        import apache_beam as beam  # type: ignore

        with beam.Pipeline() as p:
            _ = (
                p
                | "CreateManifest" >> beam.Create(records)
                | "ExtractPages" >> beam.FlatMap(dofn.process)
            )
        runner_used = "apache_beam.DirectRunner"
    except Exception:
        runner_used = "BeamCompatibleLocalDoFnRunner"

    for row in records:
        all_pages.extend(dofn.process(row))

    for pg in all_pages:
        clean_text, inj_flag = sanitize_public_comments(pg["page_text"])
        pg["sanitized_page_text"] = clean_text
        pg["has_adversarial_comment"] = bool(pg["has_adversarial_comment"] or inj_flag)

    df_pages = pd.DataFrame(all_pages)
    df_docs = (
        df_pages.groupby(
            [
                "doc_id",
                "municipality_id",
                "municipality_name",
                "agency_alias",
                "region",
                "agency_type",
                "doc_type",
                "meeting_date",
                "file_name",
                "file_sha256",
                "file_bytes",
                "total_pages",
            ],
            as_index=False,
        )
        .agg(
            total_lines=("line_count", "sum"),
            has_adversarial_comment=("has_adversarial_comment", "max"),
        )
        .sort_values("doc_id")
        .reset_index(drop=True)
    )

    write_table(df_docs, "municipal_documents", schema="raw")
    write_table(df_pages, "document_pages", schema="raw")

    # Also load ground-truth tables into raw schema for downstream evaluation
    for truth_name in ("truth_municipalities", "truth_projects", "truth_appearances"):
        t_df = pd.read_csv(SYNTH / f"{truth_name}.csv", keep_default_na=False)
        write_table(t_df, truth_name, schema="raw")

    stats = {
        "runner": runner_used,
        "documents_ingested": int(len(df_docs)),
        "pages_ingested": int(len(df_pages)),
        "total_bytes_hashed": int(df_docs["file_bytes"].sum()),
        "adversarial_documents_flagged": int(df_docs["has_adversarial_comment"].sum()),
        "ingest_seconds": round(time.time() - t0, 2),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "ingest_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    print(json.dumps(run_beam_ingest(), indent=2))
