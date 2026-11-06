# ---
# jupyter:
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 02 - Bronze
# Loads the golden extractor outputs through the real bronze runner (`backend.ingest.bronze.run`)
# into an in-memory sink, then shows them in a local Spark DataFrame (no catalog needed).

# %%
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from _env import repo_root  # noqa: E402

ROOT = repo_root()
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "fixtures" / "golden"

from backend.ingest import bronze  # noqa: E402


# %%
class MemorySink:
    def __init__(self) -> None:
        self.bronze: dict[str, list] = {}
        self.quarantine: list = []

    def write_bronze(self, extractor, records):
        self.bronze[extractor] = list(records)
        return len(records)

    def write_quarantine(self, rows):
        self.quarantine.extend(rows)
        return len(rows)


sink = MemorySink()
outcomes = bronze.run(GOLDEN, bronze.EXTRACTORS, sink)
for o in outcomes:
    print(o)
assert bronze.exit_code(outcomes) == 0
assert all(o.state == "written" and o.records > 0 for o in outcomes)

# %%
from pyspark.sql import SparkSession  # noqa: E402

spark = (
    SparkSession.builder.master("local[1]")
    .appName("nb-02-bronze")
    .config("spark.ui.enabled", "false")
    .config("spark.driver.bindAddress", "127.0.0.1")
    .config("spark.driver.host", "127.0.0.1")
    .getOrCreate()
)
rows = [(r.doc_id, r.extractor, r.page_no, r.status) for recs in sink.bronze.values() for r in recs]
df = spark.createDataFrame(rows, "doc_id string, extractor string, page_no int, status string")
summary = df.groupBy("extractor", "status").count().orderBy("extractor", "status").collect()
for row in summary:
    print(row.extractor, row.status, row["count"])
assert df.count() == len(rows) > 0
spark.stop()
