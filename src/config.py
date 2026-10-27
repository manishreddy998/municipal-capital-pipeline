"""Configuration loader and repository path definitions."""
from __future__ import annotations

import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SYNTH = DATA / "synthetic"
PDF_DIR = SYNTH / "pdfs"
REAL_DIR = DATA / "real"
RESULTS = ROOT / "results"
DOCS_IMG = ROOT / "docs" / "img"
CONFIG_PATH = ROOT / "configs" / "settings.yaml"


def load_settings() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def duckdb_path() -> Path:
    env_p = os.environ.get("DUCKDB_PATH")
    if env_p:
        p = Path(env_p)
        return p if p.is_absolute() else ROOT / p
    return DATA / "municipal_capital.duckdb"
