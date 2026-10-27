PYTHON ?= python3
export PYTHONPATH := src:.
export DBT_PROFILES_DIR := dbt

.PHONY: all synth ingest extract dbt resolve gate eval real screenshots test clean

all: synth ingest extract dbt resolve eval screenshots

synth:
	$(PYTHON) -m src.synth_corpus

ingest:
	$(PYTHON) -m src.beam_ingest

extract:
	$(PYTHON) -m src.extractor

dbt:
	$(PYTHON) -m src.warehouse --step staging
	$(PYTHON) -m src.entity_resolution
	$(PYTHON) -m src.warehouse --step marts

resolve:
	$(PYTHON) -m src.entity_resolution

gate:
	$(PYTHON) -m src.claim_gate

eval:
	$(PYTHON) -m src.evaluate

real:
	$(PYTHON) -m src.real_data

screenshots:
	$(PYTHON) scripts/render_assets.py

test:
	pytest -q

clean:
	rm -rf data/synthetic data/*.duckdb* dbt/target dbt/logs
