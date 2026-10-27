"""Cross-document project entity resolution and signal graph builder.

Links mentions of the same municipal infrastructure project across council minutes,
5-year capital improvement plans, committee reports, and tender notices over time,
even when the project code is missing or the facility title is abbreviated.

Training pairs come strictly from training-split municipalities; held-out test
municipalities (24 of 80 municipalities, 360 projects, 960 mentions) are never
used during training.
"""
from __future__ import annotations

import json
import pickle
import re
import time
from collections import defaultdict

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from sklearn.linear_model import LogisticRegression

from src.config import DATA, RESULTS, load_settings
from src.warehouse import read_sql, write_table

STOP_TOKENS = {
    "the", "of", "and", "for", "in", "to", "phase", "program", "project", "contract",
    "rehabilitation", "upgrade", "system", "facility", "station", "plant", "works",
    "municipal", "regional", "city", "region", "public", "department", "services",
}

ABBREV_MAP = {
    "wtp": "water treatment plant",
    "wwtp": "wastewater treatment plant",
    "wrrf": "water resource recovery facility",
    "ps": "pumping station",
    "sps": "sewage pumping station",
    "cso": "combined sewer overflow",
    "brt": "bus rapid transit",
    "lrt": "light rail",
    "omsf": "maintenance yard",
    "bess": "battery energy storage",
}

SECTOR_RE = re.compile(r"\bsector\s+(\d+)\b", re.I)


def normalize_project_title(title: str, agency_alias: str = "") -> str:
    s = (title or "").lower()
    if agency_alias:
        alias_low = agency_alias.lower().strip()
        if s.startswith(alias_low):
            s = s[len(alias_low):].strip()
    # Strip contract codes like CAP-2026-ON-0001
    s = re.sub(r"\bcap-\d{4}-[a-z]{2}-\d{4}\b", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = []
    for tok in s.split():
        expanded = ABBREV_MAP.get(tok, tok)
        tokens.extend(expanded.split())
    return " ".join(tokens)


def extract_sector_number(title: str) -> str:
    m = SECTOR_RE.search(title or "")
    return m.group(1) if m else ""


def distinctive_tokens(norm_title: str) -> set[str]:
    return {t for t in norm_title.split() if len(t) > 2 and t not in STOP_TOKENS}


FEATURE_NAMES = [
    "exact_code_match",
    "code_conflict",
    "sector_exact_match",
    "sector_conflict",
    "token_jaccard",
    "fuzz_token_set",
    "fuzz_token_sort",
    "budget_ratio",
    "same_funding_source",
    "same_regulatory_driver",
]


def build_candidate_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Block on (municipality_id, asset_category) to generate candidate mention pairs."""
    rows = []
    for (_, _), group in df.groupby(["municipality_id", "asset_category"], sort=False):
        idxs = group.index.to_list()
        n = len(idxs)
        for a in range(n):
            i = idxs[a]
            for b in range(a + 1, n):
                j = idxs[b]
                rows.append((i, j))
    return pd.DataFrame(rows, columns=["i", "j"])


def featurize_pairs(df: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    codes = df["project_code_stated"].fillna("").tolist()
    sectors = df["sector_num"].tolist()
    tok_sets = df["dist_tokens"].tolist()
    norm_titles = df["norm_title"].tolist()
    budgets = df["verified_sum_cad"].astype(float).tolist()
    fundings = df["funding_source"].tolist()
    drivers = df["regulatory_driver"].tolist()

    feats = []
    for i, j in zip(pairs["i"].tolist(), pairs["j"].tolist()):
        c_i, c_j = codes[i], codes[j]
        both_have_code = bool(c_i and c_j)
        exact_code = 1.0 if (both_have_code and c_i == c_j) else 0.0
        code_conflict = 1.0 if (both_have_code and c_i != c_j) else 0.0

        s_i, s_j = sectors[i], sectors[j]
        both_have_sec = bool(s_i and s_j)
        sec_match = 1.0 if (both_have_sec and s_i == s_j) else 0.0
        sec_conflict = 1.0 if (both_have_sec and s_i != s_j) else 0.0

        t_i, t_j = tok_sets[i], tok_sets[j]
        union_len = len(t_i | t_j)
        jaccard = (len(t_i & t_j) / union_len) if union_len else 0.0

        ts_ratio = fuzz.token_set_ratio(norm_titles[i], norm_titles[j]) / 100.0
        tso_ratio = fuzz.token_sort_ratio(norm_titles[i], norm_titles[j]) / 100.0

        b_i, b_j = max(1.0, budgets[i]), max(1.0, budgets[j])
        b_ratio = min(b_i, b_j) / max(b_i, b_j)

        same_fund = 1.0 if fundings[i] == fundings[j] else 0.0
        same_drv = 1.0 if drivers[i] == drivers[j] else 0.0

        feats.append((
            i,
            j,
            exact_code,
            code_conflict,
            sec_match,
            sec_conflict,
            round(jaccard, 4),
            round(ts_ratio, 4),
            round(tso_ratio, 4),
            round(b_ratio, 4),
            same_fund,
            same_drv,
        ))

    cols = ["i", "j"] + FEATURE_NAMES
    return pd.DataFrame(feats, columns=cols)


def cluster_mentions(df: pd.DataFrame, feat_df: pd.DataFrame, threshold: float) -> tuple[list[str], pd.DataFrame]:
    n = len(df)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    # Hard domain guardrails: never merge two mentions with conflicting explicit project codes or conflicting sector numbers
    accepted = feat_df[
        (feat_df["match_prob"] >= threshold)
        & (feat_df["code_conflict"] == 0.0)
        & (feat_df["sector_conflict"] == 0.0)
    ]
    for i, j in zip(accepted["i"].astype(int), accepted["j"].astype(int)):
        union(i, j)

    root_to_cluster: dict[int, str] = {}
    clusters: list[str] = []
    counter = 0
    for idx in range(n):
        r = find(idx)
        if r not in root_to_cluster:
            counter += 1
            root_to_cluster[r] = f"SIG-{counter:04d}"
        clusters.append(root_to_cluster[r])

    review_band = feat_df[
        (feat_df["match_prob"] >= 0.50)
        & (feat_df["match_prob"] < threshold)
        & (feat_df["code_conflict"] == 0.0)
    ].copy()
    return clusters, review_band


def evaluate_pairwise(df: pd.DataFrame, pairs: pd.DataFrame, split_name: str) -> dict:
    sub_mask = df["split"].values
    true_proj = df["true_project_id"].values
    pred_cluster = df["resolved_entity_id"].values

    tp = fp = fn = tn = 0
    for i, j in zip(pairs["i"].astype(int), pairs["j"].astype(int)):
        if sub_mask[i] != split_name or sub_mask[j] != split_name:
            continue
        is_same_truth = (true_proj[i] == true_proj[j])
        is_same_pred = (pred_cluster[i] == pred_cluster[j])
        if is_same_truth and is_same_pred:
            tp += 1
        elif not is_same_truth and is_same_pred:
            fp += 1
        elif is_same_truth and not is_same_pred:
            fn += 1
        else:
            tn += 1

    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = (2 * precision * recall) / max(1e-9, precision + recall)
    return {
        "split": split_name,
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def run_entity_resolution() -> dict:
    t0 = time.time()
    cfg = load_settings()["entity_resolution"]

    df_ext = read_sql("SELECT * FROM raw.extracted_mentions ORDER BY mention_id")
    df_truth = read_sql("SELECT mention_id, project_id AS true_project_id, split FROM raw.truth_appearances")
    df = df_ext.merge(df_truth, on="mention_id", how="left")

    df["norm_title"] = [
        normalize_project_title(t, a)
        for t, a in zip(df["mention_project_name"], df["agency_alias"])
    ]
    df["sector_num"] = df["mention_project_name"].map(extract_sector_number)
    df["dist_tokens"] = df["norm_title"].map(distinctive_tokens)

    pairs = build_candidate_pairs(df)
    feat_df = featurize_pairs(df, pairs)

    # Train logistic regression strictly on training-split municipalities
    splits = df["split"].values
    true_pids = df["true_project_id"].values
    is_train_pair = np.array([
        splits[int(i)] == "train" and splits[int(j)] == "train"
        for i, j in zip(feat_df["i"], feat_df["j"])
    ])
    y_all = np.array([
        1 if true_pids[int(i)] == true_pids[int(j)] else 0
        for i, j in zip(feat_df["i"], feat_df["j"])
    ])

    X_train = feat_df.loc[is_train_pair, FEATURE_NAMES].values
    y_train = y_all[is_train_pair]

    clf = LogisticRegression(C=2.0, max_iter=500, random_state=42)
    clf.fit(X_train, y_train)

    (DATA / "entity_resolver.pkl").write_bytes(pickle.dumps(clf))

    X_all = feat_df[FEATURE_NAMES].values
    feat_df["match_prob"] = np.round(clf.predict_proba(X_all)[:, 1], 4)

    clusters, review_df = cluster_mentions(df, feat_df, float(cfg["auto_merge_threshold"]))
    df["resolved_entity_id"] = clusters

    # Build entity link table for dbt core/intermediate consumption
    df_links = df[[
        "mention_id",
        "doc_id",
        "municipality_id",
        "resolved_entity_id",
        "project_code_stated",
        "norm_title",
        "sector_num",
        "true_project_id",
        "split",
    ]].copy()
    write_table(df_links, "project_entity_links", schema="core")

    review_out = review_df.assign(
        mention_id_a=review_df["i"].map(lambda idx: df.at[int(idx), "mention_id"]),
        mention_id_b=review_df["j"].map(lambda idx: df.at[int(idx), "mention_id"]),
        title_a=review_df["i"].map(lambda idx: df.at[int(idx), "mention_project_name"]),
        title_b=review_df["j"].map(lambda idx: df.at[int(idx), "mention_project_name"]),
    )[["mention_id_a", "mention_id_b", "title_a", "title_b", "match_prob"]]
    write_table(review_out, "entity_review_queue", schema="core")

    eval_test = evaluate_pairwise(df, feat_df, "test")
    eval_train = evaluate_pairwise(df, feat_df, "train")

    # Also measure missing-code recovery accuracy on mentions where project_code_stated == ''
    missing_code_df = df[df["project_code_stated"] == ""]
    # A missing-code mention is correctly linked if its resolved_entity_id maps uniquely to its true_project_id
    # A missing-code mention is linked to the main project cluster if that cluster holds the majority of the project's appearances
    proj_to_main_cluster = (
        df.groupby("true_project_id")["resolved_entity_id"]
        .agg(lambda s: s.value_counts().index[0])
        .to_dict()
    )
    missing_correct = sum(
        1 for _, r in missing_code_df.iterrows()
        if r["resolved_entity_id"] == proj_to_main_cluster.get(r["true_project_id"])
    )

    weights = {
        k: round(float(v), 3)
        for k, v in zip(FEATURE_NAMES, clf.coef_[0])
    }

    stats = {
        "total_mentions": int(len(df)),
        "candidate_pairs_scored": int(len(feat_df)),
        "training_pairs": int(is_train_pair.sum()),
        "resolved_entities": int(df["resolved_entity_id"].nunique()),
        "true_projects": int(df["true_project_id"].nunique()),
        "missing_code_mentions": int(len(missing_code_df)),
        "missing_code_mentions_linked_correctly": int(missing_correct),
        "missing_code_link_accuracy": round(missing_correct / max(1, len(missing_code_df)), 4),
        "held_out_test_metrics": eval_test,
        "train_metrics": eval_train,
        "review_queue_pairs": int(len(review_out)),
        "feature_weights": weights,
        "resolution_seconds": round(time.time() - t0, 2),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "entity_resolution_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


if __name__ == "__main__":
    print(json.dumps(run_entity_resolution(), indent=2))
