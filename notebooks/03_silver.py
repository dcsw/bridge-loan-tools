# ---
# jupyter:
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 03 - Silver
# Unifies the three extractors' bronze records into instruments and derives loans with the real
# `run_silver` (≥2-agree rule, no imputation). In-memory store, geocoder served from the cache only.

# %%
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from _env import repo_root  # noqa: E402

ROOT = repo_root()
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
TRUTH = ROOT / "tests" / "fixtures" / "truth"

import json  # noqa: E402
import tempfile  # noqa: E402

from backend.common.models import BronzeRecord  # noqa: E402
from backend.ingest.geocode import CachedGeocoder, JsonlGeocodeCache  # noqa: E402
from backend.ingest.silver_job import InMemoryStore, run_silver  # noqa: E402
from backend.ingest.unify import DocMeta  # noqa: E402

# %%
records = [
    BronzeRecord(**r)
    for path in sorted(GOLDEN.glob("*/mortgage.json"))
    for r in json.loads(path.read_text())
]
meta = {
    r.doc_id: DocMeta(
        town="mattapoisett", source_url=f"https://portal.example.test/docs/{r.doc_id}.pdf"
    )
    for r in records
}
store = InMemoryStore()
geocoder = CachedGeocoder(JsonlGeocodeCache(Path(tempfile.mkdtemp()) / "cache.jsonl"))
report = run_silver(records, meta, store, geocoder)
print(report)
assert report.instruments_merged >= 1
assert report.loans_recomputed >= 1

# %%
for inst in store.read_instruments():
    print(inst["doc_type"], inst["address_norm"], inst["principal"], inst["lender_norm"])
for loan in store.read_loans():
    print(loan["loan_id"], loan["is_bridge_loan"], loan["bridge_score"], loan["geocode_status"])
