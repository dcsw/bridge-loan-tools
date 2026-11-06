# ---
# jupyter:
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 05 - Explore `semantic_loans_v1`
# Builds silver via the real `run_silver`, registers it in DuckDB and creates the versioned view
# from `backend/ingest/sql/semantic_loans_v1.sql` (views do not persist across connections).

# %%
# ruff: noqa: E402
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from _env import repo_root

ROOT = repo_root()
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "fixtures" / "golden"

import duckdb

from backend.common.models import BronzeRecord
from backend.ingest import views
from backend.ingest.geocode import CachedGeocoder, JsonlGeocodeCache
from backend.ingest.silver_job import InMemoryStore, run_silver
from backend.ingest.unify import DocMeta

# %%
records = [
    BronzeRecord(**r)
    for path in sorted(GOLDEN.glob("*/mortgage.json"))
    for r in json.loads(path.read_text())
]
meta = {
    r.doc_id: DocMeta(town="mattapoisett", source_url=f"https://portal.example.test/{r.doc_id}.pdf")
    for r in records
}
store = InMemoryStore()
geocoder = CachedGeocoder(JsonlGeocodeCache(Path(tempfile.mkdtemp()) / "cache.jsonl"))
run_silver(records, meta, store, geocoder)

# %%
unified = [views.row_to_unified(r) for r in store.read_instruments()]
loans = [views.row_to_loan(r) for r in store.read_loans()]
con = duckdb.connect()
views.register_silver(
    con,
    views.loans_table(loans),
    views.instruments_table(unified),
)
views.create_semantic_views(con)
view = con.sql("SELECT * FROM semantic_loans_v1").to_arrow_table().to_pylist()
print(len(view), "loans in semantic_loans_v1")

# %%
assert len(view) == len(loans) > 0
assert {v["loan_id"] for v in view} == {x.loan_id for x in loans}
con.sql(
    "SELECT lender_type, count(*) AS n, avg(principal) AS avg_principal "
    "FROM semantic_loans_v1 GROUP BY 1"
).fetchall()
