"""Build-time only: download the hi_res models at EXACT Hugging Face commits.

Fails (non-zero exit) if a download does not resolve to the pinned commit. Writes
``refs/main`` to the pinned SHA so offline loading by unstructured_inference (which asks for
the default ref) resolves to exactly these bytes. File integrity is additionally checked
against ``models.sha256`` in the Dockerfile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

HUB = Path(os.environ.get("HF_HOME", "/opt/hf")) / "hub"

# repo_id -> (commit sha, files actually used by unstructured_inference)
MODELS: dict[str, tuple[str, list[str]]] = {
    "unstructuredio/yolo_x_layout": (
        "7680d6f857780bcf8d49916aa2e8881bd49dee3e",
        ["yolox_l0.05.onnx"],
    ),
    "microsoft/table-transformer-structure-recognition": (
        "f4d4bdc85c3fe4b1fa49658882a5d38bbdd0f343",
        ["config.json", "model.safetensors", "preprocessor_config.json"],
    ),
}


def main() -> int:
    for repo, (sha, files) in MODELS.items():
        path = Path(
            snapshot_download(repo_id=repo, revision=sha, allow_patterns=files, cache_dir=HUB)
        )
        if path.name != sha:
            print(f"{repo}: resolved {path.name}, expected {sha}", file=sys.stderr)
            return 1
        refs = HUB / f"models--{repo.replace('/', '--')}" / "refs"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "main").write_text(sha)
        print(f"{repo}@{sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
