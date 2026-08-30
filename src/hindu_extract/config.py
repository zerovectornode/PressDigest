"""Loads config/default.yaml into a typed, immutable settings object.

Every threshold, path, and version string used by the pipeline lives in the
YAML file, not in code. See config/default.yaml for calibration notes.
"""
from __future__ import annotations

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


@dataclass(frozen=True)
class RenderConfig:
    vision_target_megapixels: float
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
    def bronze_root(self) -> Path:
        return self.project_root / self.paths.bronze_root

    @property
    def cache_root(self) -> Path:
        return self.project_root / self.paths.cache_root

    @property
    def gold_root(self) -> Path:
        return self.project_root / self.paths.gold_root

    @property
    def raw_pdf_root(self) -> Path:
        return self.project_root / self.paths.raw_pdf_root

    @property
    def gemini_cache_root(self) -> Path:
        return self.project_root / self.paths.gemini_cache_root

    @property
    def trace_db(self) -> Path:
        return self.project_root / self.paths.trace_db

    @property
    def ranking_cache_root(self) -> Path:
        return self.project_root / self.paths.ranking_cache_root


def load_config(path: Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

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
