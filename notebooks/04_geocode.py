# ---
# jupyter:
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 04 - Geocode
# Census geocoder driven by RECORDED responses (no network): matched, low_match, no_match.

# %%
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path.cwd()))
from _env import repo_root  # noqa: E402

ROOT = repo_root()
sys.path.insert(0, str(ROOT))

from backend.ingest import geocode as g  # noqa: E402

CENSUS = ROOT / "tests" / "fixtures" / "census"
ADDRESS = "19296 Troy Shoals, Mattapoisett, MA"

# %%
seen = {}
for name in ("matched", "low_match", "no_match"):
    fixture = json.loads((CENSUS / f"{name}.json").read_text())
    result = g.CensusGeocoder(g.RecordedTransport({ADDRESS: fixture["response"]})).geocode(ADDRESS)
    print(name, result)
    assert result.status == fixture["expected_geocode_status"]
    seen[name] = result
assert seen["matched"].lat is not None
assert seen["no_match"].lat is None and seen["no_match"].lon is None  # null geometry retained
