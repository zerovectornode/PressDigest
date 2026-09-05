"""Cache layer keyed on hash(pdf_bytes) + hash(pipeline_version) + page_num.

Re-running an unchanged page is a no-op on the expensive step (pdfplumber
extraction): if a cache entry exists, it is reused as-is and only cheaply
copied into the bronze layer. Changing the PDF or bumping
pipeline_version in config/default.yaml invalidates the cache automatically
since it's part of the key - no manual cache-busting needed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from hindu_extract.config import Config


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cache_dir_for(config: Config, pdf_hash: str, version_hash: str, page_num: int) -> Path:
    return config.cache_root / pdf_hash / version_hash / f"page_{page_num:02d}"


def is_cache_complete(cache_dir: Path) -> bool:
    return (cache_dir / "page.json").exists()


def write_cache(cache_dir: Path, page_result_dict: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_dir / "page.json", "w", encoding="utf-8") as f:
        json.dump(page_result_dict, f, ensure_ascii=False, indent=2)


def read_cache(cache_dir: Path) -> dict:
    with open(cache_dir / "page.json", "r", encoding="utf-8") as f:
        return json.load(f)


def copy_cache_to(cache_dir: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache_dir / "page.json", dest_dir / "page.json")
