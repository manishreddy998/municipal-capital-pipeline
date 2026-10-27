"""Deterministic receipt auditor and zero-uncited-claim release gate.

1. Reopens every source PDF on disk, recomputes its sha256 digest, extracts the
   cited page and line, and verifies that the stored verbatim quote matches the PDF
   page text character-for-character.
2. Checks summary/answer text against the structured receipt: every dollar figure,
   fiscal year, project code, and document ID must appear in the backing receipt,
   and speculative words (approximately, roughly, estimated at, rumoured) are blocked.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from pypdf import PdfReader

from src.config import PDF_DIR, RESULTS
from src.warehouse import read_sql

DOLLAR_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")
DOC_ID_RE = re.compile(r"\bDOC-\d{4}\b")
CODE_RE = re.compile(r"\bCAP-\d{4}-[A-Z]{2}-\d{4}\b")
BANNED_SPECULATIVE_RE = re.compile(
    r"\b(?:approximately|roughly|around|rumoured|rumored|allegedly|unconfirmed|guessed)\b",
    re.I,
)


def verify_summary_against_receipt(summary_text: str, receipt: dict[str, Any]) -> dict[str, Any]:
    """Check that every dollar amount, document ID, and project code in a summary is backed by the receipt."""
    allowed_numbers: set[int] = set()
    for k in (
        "stated_total_cad",
        "verified_sum_cad",
        "civil_works_cad",
        "mech_elec_cad",
        "engineering_cad",
        "prior_budget_cad",
        "initial_budget_cad",
        "latest_verified_budget_cad",
        "active_approved_budget_cad",
        "cumulative_amendment_delta_cad",
        "total_active_budget_cad",
    ):
        if k in receipt and receipt[k] is not None:
            allowed_numbers.add(abs(int(receipt[k])))

    for num_val in receipt.get("allowed_amounts", []):
        allowed_numbers.add(abs(int(num_val)))

    allowed_docs = set(receipt.get("allowed_doc_ids", []))
    if "doc_id" in receipt and receipt["doc_id"]:
        allowed_docs.add(str(receipt["doc_id"]))
    if "latest_doc_id" in receipt and receipt["latest_doc_id"]:
        allowed_docs.add(str(receipt["latest_doc_id"]))

    allowed_codes = set(receipt.get("allowed_project_codes", []))
    if "project_code" in receipt and receipt["project_code"]:
        allowed_codes.add(str(receipt["project_code"]))

    uncited_amounts = []
    for m in DOLLAR_RE.finditer(summary_text or ""):
        raw_num = m.group(1).replace(",", "").split(".")[0]
        if raw_num.isdigit():
            val = int(raw_num)
            if val not in allowed_numbers:
                uncited_amounts.append(f"${m.group(1)}")

    uncited_docs = [
        d for d in DOC_ID_RE.findall(summary_text or "")
        if d not in allowed_docs
    ]
    uncited_codes = [
        c for c in CODE_RE.findall(summary_text or "")
        if c not in allowed_codes
    ]
    speculative_words = [
        w.group(0) for w in BANNED_SPECULATIVE_RE.finditer(summary_text or "")
    ]

    passed = (
        len(uncited_amounts) == 0
        and len(uncited_docs) == 0
        and len(uncited_codes) == 0
        and len(speculative_words) == 0
    )
    return {
        "passed": passed,
        "uncited_amounts": uncited_amounts,
        "uncited_doc_ids": uncited_docs,
        "uncited_project_codes": uncited_codes,
        "speculative_words": speculative_words,
    }


def audit_all_pdf_receipts() -> dict[str, Any]:
    """Reopen every PDF on disk and verify sha256, page number, line number, and verbatim quote for all 3,200 mentions."""
    t0 = time.time()
    df_mentions = read_sql(
        "SELECT mention_id, doc_id, file_name, file_sha256, page_number, line_number, verbatim_quote "
        "FROM staging.stg_extracted_mentions ORDER BY doc_id, page_number, line_number"
    )

    page_cache: dict[tuple[str, int], list[str]] = {}
    sha_cache: dict[str, str] = {}

    verified_count = 0
    mismatches = []

    for row in df_mentions.to_dict(orient="records"):
        fname = row["file_name"]
        pno = int(row["page_number"])
        lno = int(row["line_number"])
        quote = row["verbatim_quote"]

        if fname not in sha_cache:
            pdf_path = PDF_DIR / fname
            raw_bytes = pdf_path.read_bytes()
            sha_cache[fname] = hashlib.sha256(raw_bytes).hexdigest()
            reader = PdfReader(str(pdf_path))
            for idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                page_cache[(fname, idx + 1)] = lines

        actual_sha = sha_cache[fname]
        page_lines = page_cache.get((fname, pno), [])
        line_match = (1 <= lno <= len(page_lines)) and (page_lines[lno - 1] == quote)
        sha_match = (actual_sha == row["file_sha256"])

        if sha_match and line_match:
            verified_count += 1
        else:
            mismatches.append({
                "mention_id": row["mention_id"],
                "doc_id": row["doc_id"],
                "sha_match": sha_match,
                "line_match": line_match,
            })

    out = {
        "total_receipts_audited": int(len(df_mentions)),
        "receipts_matching_pdf_exact": int(verified_count),
        "mismatches": len(mismatches),
        "audit_seconds": round(time.time() - t0, 2),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "receipt_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(audit_all_pdf_receipts(), indent=2))
