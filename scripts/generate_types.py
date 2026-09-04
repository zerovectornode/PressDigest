"""Generates frontend/src/types/api.ts from the Pydantic models in
hindu_extract.api.schemas, so the API contract is enforced at compile time
instead of hand-copied and allowed to drift.

Run whenever a schema in hindu_extract/api/schemas.py changes:
    python scripts/generate_types.py

Requires Node (uses the json-schema-to-typescript package already listed in
frontend/package.json's devDependencies) - run `npm install` in frontend/
first if that hasn't been done yet.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from hindu_extract.api import schemas

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "frontend" / "src" / "types" / "api.ts"
TMP_DIR = PROJECT_ROOT / "scripts" / "_generated_schemas"

MODELS = [
    schemas.ParsedMetadataOut,
    schemas.PagePhaseOut,
    schemas.JobStatusOut,
    schemas.StartJobOut,
    schemas.EditionSummaryOut,
    schemas.EditionDetailOut,
    schemas.PageStatusOut,
    schemas.ArticleOut,
    schemas.PageOut,
    schemas.PageArticlesOut,
    schemas.RunSummaryOut,
    schemas.StageEventOut,
    schemas.RunDetailOut,
    schemas.PageRawOut,
    schemas.QuotaOut,
    schemas.RankedArticleOut,
    schemas.DuplicateContinuationOut,
    schemas.ExcludedArticleOut,
    schemas.RankingOut,
]


def _strip_property_titles(node: object) -> None:
    """Pydantic gives every field its own "title" (e.g. "Edition" for a
    field named `edition`), which makes json2ts hoist each field into its
    own top-level named alias - colliding across models that happen to
    share a field name (multiple unrelated "Edition" aliases end up
    declared in the same file). Stripping "title" from every property
    schema (at any nesting depth, including inside $defs) makes json2ts
    inline field types directly in the interface instead."""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            for prop_schema in properties.values():
                if isinstance(prop_schema, dict):
                    prop_schema.pop("title", None)
        for value in node.values():
            _strip_property_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_property_titles(item)


_INTERFACE_START = re.compile(r"export interface (\w+) \{")


def _dedupe_interfaces(source: str) -> str:
    """Each model is run through json2ts independently, so a model
    referenced by another (e.g. PagePhaseOut inside JobStatusOut's $defs)
    gets its interface emitted once per referencing model. TypeScript's
    declaration merging can't reconcile two identical index signatures
    ([k: string]: unknown) on the same interface name, so exact
    re-declarations must be dropped rather than left for the compiler."""
    seen: dict[str, str] = {}
    out: list[str] = []
    pos = 0
    for match in _INTERFACE_START.finditer(source):
        out.append(source[pos : match.start()])
        name = match.group(1)
        depth = 1
        end = match.end()
        while depth > 0:
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
            end += 1
        block = source[match.start() : end]
        if name in seen:
            if seen[name] != block:
                raise ValueError(f"conflicting duplicate definitions for {name!r}")
        else:
            seen[name] = block
            out.append(block)
        pos = end
    out.append(source[pos:])
    return "".join(out)


def main() -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    npx = "npx.cmd" if sys.platform == "win32" else "npx"

    header = (
        "/* eslint-disable */\n"
        "/**\n"
        " * AUTO-GENERATED from hindu_extract/api/schemas.py by scripts/generate_types.py.\n"
        " * Do not edit by hand - the Pydantic models are the source of truth.\n"
        " */\n\n"
    )
    pieces = [header]

    for model in MODELS:
        schema = model.model_json_schema()
        schema["title"] = model.__name__
        _strip_property_titles(schema)
        schema_path = TMP_DIR / f"{model.__name__}.json"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

        result = subprocess.run(
            [npx, "json2ts", "--input", str(schema_path), "--bannerComment", ""],
            cwd=PROJECT_ROOT / "frontend",
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
        pieces.append(result.stdout.strip() + "\n")

    for f in TMP_DIR.glob("*.json"):
        f.unlink()
    TMP_DIR.rmdir()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(_dedupe_interfaces("\n".join(pieces)), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
