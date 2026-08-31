"""Loads config/default.yaml into a typed, immutable settings object.

Every threshold, path, and version string used by the pipeline lives in the
YAML file, not in code. See config/default.yaml for calibration notes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


@dataclass(frozen=True)
class Thresholds:
    size_outlier_ratio: float
    kerning_gap_ratio: float
    span_break_gap_ratio: float
    row_band_tolerance_ratio: float
    word_space_gap_ratio: float


@dataclass(frozen=True)
class RenderConfig:
    hires_dpi: int


@dataclass(frozen=True)
class Paths:
    bronze_root: Path
    cache_root: Path
    gold_root: Path
    gemini_cache_root: Path
    raw_pdf_root: Path
    trace_db: Path
    ranking_cache_root: Path


@dataclass(frozen=True)
class CanaryStyleConfig:
    bold_markers: tuple[str, ...]
    italic_markers: tuple[str, ...]


@dataclass(frozen=True)
class GeminiConfig:
    model: str
    thinking_level: str
    temperature: float
    max_output_tokens: int
    prompt_version: str


@dataclass(frozen=True)
class ConcurrencyConfig:
    max_concurrent: int
    requests_per_minute: int
    requests_per_day: int
    tokens_per_minute: int
    estimated_tokens_per_request: int


@dataclass(frozen=True)
class RankingConfig:
    model: str
    thinking_level: str
    temperature: float
    max_output_tokens: int
    prompt_version: str
    body_preview_words: int
    top_n: int


@dataclass(frozen=True)
class Config:
    pipeline_version: str
    paths: Paths
    thresholds: Thresholds
    render: RenderConfig
    style: CanaryStyleConfig
    gemini: GeminiConfig
    concurrency: ConcurrencyConfig
    ranking: RankingConfig
    project_root: Path

    @property
    def data_anchor(self) -> Path:
        """Where paths.* (all of which are "data/..." strings, e.g.
        "data/bronze") resolve relative to. Defaults to project_root, same
        as always; HINDU_EXTRACT_DATA_ROOT overrides it so a deployment can
        put actual pipeline output on a separate volume/directory from the
        application code without touching a single path string in
        config/default.yaml - see design/DESIGN.md "Deployment: GCP
        e2-micro VM" (data lives at /var/lib/pressdigest, app code at
        /opt/pressdigest/app, so a redeploy's rsync - which never touches
        /var/lib/pressdigest - can't lose extracted editions). Checked live
        rather than baked in at load_config time so it behaves like every
        other env override in this module.
        """
        override = os.environ.get("HINDU_EXTRACT_DATA_ROOT")
        return Path(override) if override else self.project_root

    @property
    def bronze_root(self) -> Path:
        return self.data_anchor / self.paths.bronze_root

    @property
    def cache_root(self) -> Path:
        return self.data_anchor / self.paths.cache_root

    @property
    def gold_root(self) -> Path:
        return self.data_anchor / self.paths.gold_root

    @property
    def raw_pdf_root(self) -> Path:
        return self.data_anchor / self.paths.raw_pdf_root

    @property
    def gemini_cache_root(self) -> Path:
        return self.data_anchor / self.paths.gemini_cache_root

    @property
    def trace_db(self) -> Path:
        return self.data_anchor / self.paths.trace_db

    @property
    def ranking_cache_root(self) -> Path:
        return self.data_anchor / self.paths.ranking_cache_root



# Environment-variable overrides applied on top of the YAML, checked at
# load time rather than baked into a separate production.yaml - see
# design/DESIGN.md "Deployment: GCP e2-micro VM" for why a second full
# config file (mostly identical to default.yaml) was rejected in favor of
# this: no risk of the two files drifting apart on everything except the
# one value production actually needs to differ on. HINDU_EXTRACT_MAX_
# CONCURRENT is the one the e2-micro deployment sets (2, down from 4) as
# a safety margin against its shared-core CPU; unset in local dev, so
# default.yaml's own value is used unchanged.
_ENV_OVERRIDES = {
    "HINDU_EXTRACT_MAX_CONCURRENT": ("concurrency", "max_concurrent", int),
}


def _apply_env_overrides(raw: dict) -> dict:
    for env_var, (section, key, cast) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        raw = {**raw, section: {**raw[section], key: cast(value)}}
    return raw


def load_config(path: Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw = _apply_env_overrides(raw)

    project_root = config_path.resolve().parents[1]

    return Config(
        pipeline_version=raw["pipeline_version"],
        project_root=project_root,
        paths=Paths(
            bronze_root=Path(raw["paths"]["bronze_root"]),
            cache_root=Path(raw["paths"]["cache_root"]),
            gold_root=Path(raw["paths"]["gold_root"]),
            gemini_cache_root=Path(raw["paths"]["gemini_cache_root"]),
            raw_pdf_root=Path(raw["paths"]["raw_pdf_root"]),
            trace_db=Path(raw["paths"]["trace_db"]),
            ranking_cache_root=Path(raw["paths"]["ranking_cache_root"]),
        ),
        thresholds=Thresholds(**raw["thresholds"]),
        render=RenderConfig(**raw["render"]),
        style=CanaryStyleConfig(
            bold_markers=tuple(raw["canary"]["bold_markers"]),
            italic_markers=tuple(raw["canary"]["italic_markers"]),
        ),
        gemini=GeminiConfig(**raw["gemini"]),
        concurrency=ConcurrencyConfig(**raw["concurrency"]),
        ranking=RankingConfig(**raw["ranking"]),
    )
