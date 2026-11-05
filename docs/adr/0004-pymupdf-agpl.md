# ADR-0004: PyMuPDF (AGPL) in the extractor sandbox, and extractor image footprint

Status: accepted for Phase 1 (internal, single-user, not distributed). Revisit at the triggers below.

This is an engineering assessment, not legal advice.

## Context

Design 3.5 requires every PDF parser to run in an unprivileged, no-network sandbox and says
"PyMuPDF is AGPL: license reviewed in an ADR". We run three extractors so silver can use a
>=2-agree rule: PyMuPDF (text layer, fast), Docling (layout + TableFormer + OCR) and
Unstructured (hi_res + Tesseract).

PyMuPDF (and MuPDF underneath) is dual-licensed by Artifex: **AGPL-3.0** or a paid commercial
license. We use the AGPL grant. Docling (MIT), Unstructured (Apache-2.0) and their transitive
parsers (pypdfium2 BSD/Apache, pdfminer.six MIT) are permissive.

## What the AGPL means for this project

* **Copyleft scope.** Code that links PyMuPDF into one program forms a combined work that must be
  offered under AGPL-3.0. In this repo exactly one module imports it:
  `backend/ingest/extractors/pymupdf/extract.py` (about 40 lines), in its own image. It talks to
  the rest of the system only through files (`data/extract-in` -> `data/extract-out/*.json`);
  no import, RPC or shared memory. We treat that as an arm's-length boundary. This is the usual
  reading of the FSF position for separate programs exchanging data, but it is a judgment, not
  a tested-in-court fact.
* **Section 13 (network clause)** only bites if we *modify* PyMuPDF and let users interact with it
  remotely. We use the unmodified PyPI wheel, and the extractor has no network surface at all
  (`network_mode: none`, one-shot job).
* **Extracted text is not a derivative work** of the library; AGPL does not reach the data it processes.
* **Conveying the image** (pushing it to a registry others can pull, shipping it to a customer, or
  running it for third parties as a hosted service) would obligate us to provide the corresponding
  source of that image: the PyMuPDF source (available from Artifex/PyPI sdist) and our `extract.py`
  plus the shared runner it imports. That is cheap to comply with but must be done deliberately.
* **Today:** images are built and run locally by one operator on their own machine; nothing is
  conveyed to anyone. No AGPL obligation is triggered in Phase 1.

## Decision

1. Keep PyMuPDF for Phase 1 as the cheap text-layer extractor, **confined to its own image**
   with the only importing module being `extractors/pymupdf/extract.py`. Nothing else in the repo
   may `import pymupdf`/`fitz` (reviewers: grep for it).
2. Pin the exact version (`pymupdf==1.27.2`, hash-locked in `uv.lock` and the exported
   requirements); the pinned version is recorded in every bronze record (`extractor_version`).
3. Do not use PyMuPDF's separately licensed add-ons (`pymupdf-layout`, `pymupdf4llm`, Pro);
   the runtime hint to install `pymupdf_layout` is ignored.
4. If any trigger below happens, either buy the Artifex commercial license **or** drop the
   PyMuPDF extractor.

**Triggers to revisit:** publishing the image anywhere others can pull it; any hosted/multi-user
deployment (Phase 2+ cloud) where users reach the extractor's output through our service
in a way counsel considers "interaction"; modifying PyMuPDF; counsel disagreeing with the
arm's-length reading above.

## Alternatives considered

* **pypdfium2 / pdfminer.six** (permissive): already installed transitively via Docling and
  Unstructured. A drop-in replacement for the text-layer role is a day of work and is the
  designated fallback. Cost: the unify >=2-agree rule would then compare extractors that share
  a PDF engine (Docling's parser vs pypdfium2), weakening independence.
* **Commercial Artifex license:** removes the question; cost/terms unknown, not worth it for
  a single-user Phase 1.
* **No third text-layer extractor:** the two ML extractors alone cannot form a majority.

## Consequences

* The AGPL surface is one small file in one image; removal is a delete of that directory, its
  compose service and the `pymupdf` dependency group.
* `docs` and release checklists must include "image conveyed?" before any publish step.

## Extractor sandbox and measured footprint

Common sandbox (all three, `deploy/docker/compose.extractors.yaml`): `network_mode: none` in the
service definition, read-only root FS, `/tmp` tmpfs 512 MB, non-root user (`EXTRACT_UID`, host user
by default via make; image default 10001), `cap_drop: ALL`, `no-new-privileges`, `pids_limit: 256`,
CPU 2, memory 1 GB (pymupdf) / 6 GB (docling, unstructured), per-document wall-clock timeout
(default 300 s, child process hard-killed), pre-validation (5 MiB, `%PDF-` magic, `%%EOF`,
200-page cap; the pre-validation page count is a regex heuristic, so each extractor also enforces the cap with a real parser, see below), `/in` read-only bind, `/out` the only writable bind, no docker socket, base image
digest-pinned, dependencies hash-pinned and exported from `uv.lock`, CPU-only torch wheels.
Models are baked at build time (Docling layout + TableFormer + RapidOCR; Unstructured
yolox hi_res layout + table-transformer; Tesseract `eng`); runtime sets `HF_HUB_OFFLINE=1`.
Unstructured 0.27.x ships no NLTK dependency, so no NLTK data is baked.

Scanned/OCR rule: Docling and Unstructured OCR image-only pages (RapidOCR / Tesseract).
PyMuPDF never OCRs. Any page for which an extractor ends up with no text is emitted
`status=partial`, `error="needs_ocr: ..."` (PyMuPDF on `scanned_image.pdf`, golden-tested).

Measured on this dev machine (WSL2, 4 cores, 15 GB RAM, warm base image, network available at
build, `--no-cache` builds):

| Image | Clean build time | Disk size | Compressed content | Notes |
|---|---|---|---|---|
| `extract-pymupdf` | 10 s | 268 MB | 69.8 MB | python:3.12-slim + one wheel |
| `extract-docling` | 259 s (4 m 19 s) | 4.34 GB | 1.36 GB | pip layer 1.86 GB (CPU torch + deps), baked models 733 MB |
| `extract-unstructured` | 314 s (5 m 14 s) | 4.47 GB | 1.2 GB | includes Tesseract + yolox + table-transformer |

Run time: the whole docker test module (all three extractors over the 7 fixtures plus the isolation tests, images already built) took 166 s on this machine; a docling run over the fixtures took about 43 s including model load (measured via `regen_goldens.py`). pymupdf is sub-second per document; per-extractor figures for unstructured were not measured separately. Build times need network (PyPI, PyTorch CPU
index, Hugging Face, Debian mirrors) and exclude the first pull of the base image. The images are
large mainly because of torch and ML runtimes; CPU-only wheels are already used (the CUDA wheels
would add several GB). Further shrinking (ONNX-only inference, dropping unused Docling model
variants) is possible but not attempted in Phase 1. `apt-get` packages in the Docling/Unstructured
images are not version-pinned (only the base image digest and the Python wheels are); trivy in CI
covers them.

## Page cap enforcement (what is actually enforced)

Pre-validation counts pages heuristically (regex) and can be bypassed by pages hidden in
compressed object streams. The authoritative cap is inside each extractor, and each reports
`truncated` so the runner marks the last emitted page `partial` / `page_cap`:

* PyMuPDF: iterates `min(doc.page_count, max_pages)`.
* Docling: `convert(..., page_range=(1, max_pages))` (found by test: `max_num_pages` raises instead of truncating); truncation from `result.input.page_count`.
* Unstructured: pypdf counts pages and slices the PDF to `max_pages` *before* `partition_pdf`
  (hi_res previously ran on every page and only filtered elements afterwards).

Tested by calling each extractor directly (bypassing pre-validation) on the 3-page fixture with
a cap of 2 (`test_extractor_itself_enforces_page_cap`). On timeout the runner now kills the
child's whole process group, so grandchildren such as Tesseract are not orphaned (tested).

## Model pinning (reproducibility)

Unstructured models are downloaded by `unstructured/bake_models.py` at exact commits, the build
fails on any mismatch, `refs/main` is written to the pinned SHA, and every baked file is checked
against `unstructured/models.sha256`:

* `unstructuredio/yolo_x_layout` @ `7680d6f857780bcf8d49916aa2e8881bd49dee3e`
* `microsoft/table-transformer-structure-recognition` @ `f4d4bdc85c3fe4b1fa49658882a5d38bbdd0f343`

`docling-tools models download` cannot pin revisions (they follow the pinned docling 2.148.0).
The commits fetched by the current build were (from the HF download metadata):
`docling-layout-heron` 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8,
`docling-layout-heron-onnx` 40bde044036bb181c130ddf6c51792187268748f,
`docling-models` (TableFormer) fc0f2d45e2218ea24bce5045f58a389aed16dc23; the RapidOCR files
have no commit, so the real pin is `docling/models.sha256` (sha256 of every baked file,
including RapidOCR), checked at build time with `sha256sum -c` and again by
`test_baked_models_match_sha256_manifest`. If Docling's upstream model repos change, the Docling
build fails loudly instead of silently producing different goldens. Goldens are tied to these
models through the pinned library versions in `extractor_version` plus the manifests.
