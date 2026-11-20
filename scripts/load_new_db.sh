#!/bin/bash
# Load the 70% train split into the NEW database (never the old one) and embed it.
set -e
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export PG_DATABASE=${NEW_DB:-Knowledgeops_gw_1536} AZURE_PG_DATABASE=${NEW_DB:-Knowledgeops_gw_1536}
export KB_DATA_DIR=$PWD/data/split/train
PY=${PY:-python}
(cd backend && $PY step3_populate_data.py && $PY step4_store_embeddings.py)
