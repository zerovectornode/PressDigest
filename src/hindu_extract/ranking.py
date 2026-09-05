"""Edition-wide importance ranking: one Gemini call sees every article in
the edition together and returns the top N by importance.

Per-page ranking would produce non-comparable scores - a weak page's best
article would look artificially important with nothing on that page to
compete against. This call ranks across the whole edition instead, exactly
once, using only what Phase 2/3 already extracted (headline, deck, a body
preview, and continues_on_page) - no new extraction happens here.

This is the first model call in the pipeline whose OUTPUT is prose the
model wrote (`why_it_matters`), not a fact about text we already have. That
field is kept in its own column, clearly separate from any extracted
article text, specifically so it can never be mistaken for or merged into
verbatim content - see RankedArticle below and design/DESIGN.md.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from hindu_extract.config import Config
from hindu_extract.ranking_prompt import (
    CATEGORIES,
    EXCLUSION_REASON_CODES,
    EXCLUSION_RISKS,
    MAX_EXCLUDED_ENTRIES,
    build_system_prompt,
    build_user_prompt,
    response_schema,
)
from hindu_extract.trace import RunTracer

ARTICLE_ID_SEP = "-"


class RankingError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateArticle:
    article_id: str  # composite: "p{page:02d}-{original_article_id}"
    page: int
    headline: str
    deck: str
    continues_on_page: int | None
    body_preview: str

    def to_prompt_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "page": self.page,
            "headline": self.headline,
            "deck": self.deck,
            "continues_on_page": self.continues_on_page,
            "body_preview": self.body_preview,
        }


@dataclass(frozen=True)
class RankedArticle:
    article_id: str
    page: int
    headline: str
    rank: int
    importance_score: int
    category: str
    why_it_matters: str  # MODEL-GENERATED - never extracted article text
    exclusion_risk: str

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "page": self.page,
            "headline": self.headline,
            "rank": self.rank,
            "importance_score": self.importance_score,
            "category": self.category,
            "why_it_matters": self.why_it_matters,
            "exclusion_risk": self.exclusion_risk,
        }


@dataclass(frozen=True)
class ExcludedArticle:
    article_id: str
    page: int
    headline: str
    reason_code: str
    note: str  # MODEL-GENERATED - never extracted article text

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "page": self.page,
            "headline": self.headline,
            "reason_code": self.reason_code,
            "note": self.note,
        }


@dataclass(frozen=True)
class DuplicateContinuationIssue:
    first_part_id: str
    first_part_page: int
    continues_on_page: int
    conflicting_id: str

    def to_dict(self) -> dict:
        return {
            "first_part_id": self.first_part_id,
            "first_part_page": self.first_part_page,
            "continues_on_page": self.continues_on_page,
            "conflicting_id": self.conflicting_id,
        }


@dataclass(frozen=True)
class RankingValidationResult:
    ok: bool
    issues: tuple[str, ...] = field(default_factory=tuple)
    duplicate_continuations: tuple[DuplicateContinuationIssue, ...] = field(default_factory=tuple)


@dataclass
class RankingOutcome:
    ranked: list[RankedArticle]
    excluded: list[ExcludedArticle]
    validation: RankingValidationResult
    usage: dict
    retried: bool
    eligible_count_note: str | None

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_token_count") or 0

    @property
    def all_cached(self) -> bool:
        return bool(self.usage.get("cache_hit"))


def build_corpus(config: Config, edition: str, date: str) -> list[CandidateArticle]:
    """Reads every page's gold JSON for an edition (no API calls) and
    builds one flat candidate list. article_id is made globally unique by
    prefixing the page number, since gold JSON's per-page article_ids
    ("1", "2", ...) are reused independently on every page."""
    edition_dir = config.gold_root / edition / date
    candidates: list[CandidateArticle] = []
    for page_dir in sorted(edition_dir.glob("page_*")):
        articles_path = page_dir / "articles.json"
        if not articles_path.exists():
            continue
        page_num = int(page_dir.name.split("_")[1])
        gold = json.loads(articles_path.read_text(encoding="utf-8"))
        for a in gold.get("articles") or []:
            words = (a.get("body") or "").split()
            preview = " ".join(words[: config.ranking.body_preview_words])
            candidates.append(
                CandidateArticle(
                    article_id=f"p{page_num:02d}{ARTICLE_ID_SEP}{a['article_id']}",
                    page=page_num,
                    headline=a.get("headline") or "",
                    deck=" ".join(a.get("deck") or []),
                    continues_on_page=a.get("continues_on_page"),
                    body_preview=preview,
                )
            )
    return candidates


def _cache_key(user_prompt: str, prompt_version: str, model: str, thinking_level: str, max_output_tokens: int) -> str:
    h = hashlib.sha256()
    for part in (user_prompt, prompt_version, model, thinking_level, str(max_output_tokens)):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:24]


def _cache_path(config: Config, key: str) -> Path:
    return config.ranking_cache_root / f"{key}.json"


def _call_gemini_for_ranking(
    corpus: list[CandidateArticle],
    config: Config,
    use_cache: bool,
    tracer: RunTracer | None,
    extra_instruction: str | None = None,
) -> tuple[dict, dict]:
    user_prompt = build_user_prompt([c.to_prompt_dict() for c in corpus])
    if extra_instruction:
        user_prompt = f"{user_prompt}\n\n{extra_instruction}"

    key = _cache_key(
        user_prompt,
        config.ranking.prompt_version,
        config.ranking.model,
        config.ranking.thinking_level,
        config.ranking.max_output_tokens,
    )
    cache_path = _cache_path(config, key)

    stage_ctx = tracer.stage(0, "ranking") if tracer is not None else nullcontext({})
    with stage_ctx as detail:
        detail["model"] = config.ranking.model
        detail["thinking_level"] = config.ranking.thinking_level
        detail["candidate_count"] = len(corpus)

        if use_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            usage = dict(cached["usage"])
            usage["cache_hit"] = True
            detail.update(usage)
            if tracer is not None:
                tracer.record_gemini_raw(0, user_prompt, json.dumps(cached["response"], ensure_ascii=False))
            return cached["response"], usage

        from google import genai
        from google.genai import types
        from hindu_extract.gemini_client import _generate_with_retry, _get_api_key

        client = genai.Client(api_key=_get_api_key())
        # page_num=0: ranking's edition-wide sentinel (see trace.py) - reused
        # here purely for the retry ladder's log/trace messages, not a real page.
        response = _generate_with_retry(
            client,
            config,
            0,
            detail,
            model=config.ranking.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=build_system_prompt(config.ranking.top_n),
                temperature=config.ranking.temperature,
                max_output_tokens=config.ranking.max_output_tokens,
                response_mime_type="application/json",
                response_json_schema=response_schema(config.ranking.top_n),
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel[config.ranking.thinking_level]
                ),
            ),
        )

        usage = {"cache_hit": False}
        if response.usage_metadata:
            um = response.usage_metadata
            usage.update(
                {
                    "prompt_token_count": um.prompt_token_count,
                    "candidates_token_count": um.candidates_token_count,
                    "thoughts_token_count": um.thoughts_token_count,
                    "total_token_count": um.total_token_count,
                }
            )
        detail.update(usage)

        finish_reason = response.candidates[0].finish_reason if response.candidates else None
        detail["finish_reason"] = str(finish_reason) if finish_reason is not None else None
        if finish_reason is not None and str(finish_reason).endswith("MAX_TOKENS"):
            raise RankingError(
                f"ranking response truncated at max_output_tokens="
                f"{config.ranking.max_output_tokens} (thoughts={usage.get('thoughts_token_count')}, "
                f"candidates={usage.get('candidates_token_count')})"
            )
        if not response.text:
            raise RankingError(f"empty ranking response from Gemini (finish_reason={finish_reason})")

        parsed = json.loads(response.text)

        if tracer is not None:
            tracer.record_gemini_raw(0, user_prompt, response.text)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"response": parsed, "usage": usage}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return parsed, usage


_HEADLINE_STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "as", "at",
    "with", "after", "over", "amid", "by", "from", "its", "it", "than",
}


def _headline_words(headline: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in headline).split() if w not in _HEADLINE_STOPWORDS}


def _headlines_likely_same_story(a: str, b: str) -> bool:
    """Newspapers commonly repeat a shortened "jump head" on a story's
    continuation page rather than leaving it headline-less - verified live
    (page 8: "Karki is Nepal's first woman Prime Minister" is the actual
    jump head for the page-1 "Karki is Nepal's first woman PM" story). Page
    number alone is NOT enough to detect this: a continuation's target page
    can easily also contain other, completely unrelated top-ranked stories
    (verified live: page 8 has 10 articles, most with no relation to either
    continuation) - checking headline word overlap is what actually
    distinguishes "this is the same story's jump head" from "this just
    happens to be on the same page"."""
    words_a, words_b = _headline_words(a), _headline_words(b)
    if not words_a or not words_b:
        return False
    shared = words_a & words_b
    return len(shared) >= 2 and len(shared) / min(len(words_a), len(words_b)) >= 0.5


def _validate(parsed: dict, corpus_by_id: dict[str, CandidateArticle], top_n: int) -> RankingValidationResult:
    issues: list[str] = []
    ranked_raw = parsed.get("ranked") or []

    seen_ids: set[str] = set()
    for entry in ranked_raw:
        aid = entry.get("article_id")
        if aid not in corpus_by_id:
            issues.append(f"article_id {aid!r} does not exist in the input corpus")
        if aid in seen_ids:
            issues.append(f"duplicate article_id {aid!r} in ranked list")
        seen_ids.add(aid)

        category = entry.get("category")
        if category not in CATEGORIES:
            issues.append(f"article {aid!r}: category {category!r} is not in the fixed enum")

        risk = entry.get("exclusion_risk")
        if risk not in EXCLUSION_RISKS:
            issues.append(f"article {aid!r}: exclusion_risk {risk!r} is not in the fixed enum")

        score = entry.get("importance_score")
        if not isinstance(score, int) or not (0 <= score <= 100):
            issues.append(f"article {aid!r}: importance_score {score!r} is not an integer in 0-100")

        why = entry.get("why_it_matters") or ""
        if len(why.split()) > 40:  # soft margin over the requested 30-word cap
            issues.append(f"article {aid!r}: why_it_matters is {len(why.split())} words, well over the 30-word cap")

    if len(ranked_raw) > top_n:
        issues.append(f"returned {len(ranked_raw)} articles, expected at most {top_n}")

    excluded_raw = parsed.get("excluded") or []
    ranked_id_set = {e.get("article_id") for e in ranked_raw}
    seen_excluded_ids: set[str] = set()
    for entry in excluded_raw:
        aid = entry.get("article_id")
        if aid not in corpus_by_id:
            issues.append(f"excluded article_id {aid!r} does not exist in the input corpus")
        if aid in seen_excluded_ids:
            issues.append(f"duplicate article_id {aid!r} in excluded list")
        seen_excluded_ids.add(aid)
        if aid in ranked_id_set:
            issues.append(f"article {aid!r} appears in both ranked and excluded lists")
        reason = entry.get("reason_code")
        if reason not in EXCLUSION_REASON_CODES:
            issues.append(f"excluded article {aid!r}: reason_code {reason!r} is not in the fixed enum")
    if len(excluded_raw) > MAX_EXCLUDED_ENTRIES:
        issues.append(f"excluded list has {len(excluded_raw)} entries, expected at most {MAX_EXCLUDED_ENTRIES}")

    # Rank/score consistency: score must be non-increasing as rank increases.
    by_rank = sorted(ranked_raw, key=lambda e: e.get("rank", 0))
    prev_score = None
    for entry in by_rank:
        score = entry.get("importance_score")
        if isinstance(score, int) and prev_score is not None and score > prev_score:
            issues.append(
                f"rank {entry.get('rank')} (score {score}) scores higher than a lower rank number (score {prev_score})"
            )
        if isinstance(score, int):
            prev_score = score

    # Cross-check for duplicate continuations: for every ranked article that
    # is itself a known first-part (continues_on_page set), check whether
    # any OTHER ranked article on that target page is actually the SAME
    # story's jump head (headline word overlap - see
    # _headlines_likely_same_story), not just any article that happens to
    # share a page number with the continuation target. A real
    # cross-reference against the actual data, not trusting the model's own
    # self-report.
    duplicate_continuations: list[DuplicateContinuationIssue] = []
    ranked_ids = {e.get("article_id") for e in ranked_raw}
    for entry in ranked_raw:
        aid = entry.get("article_id")
        candidate = corpus_by_id.get(aid)
        if candidate is None or candidate.continues_on_page is None:
            continue
        for other_id in ranked_ids:
            if other_id == aid:
                continue
            other = corpus_by_id.get(other_id)
            if (
                other is not None
                and other.page == candidate.continues_on_page
                and _headlines_likely_same_story(candidate.headline, other.headline)
            ):
                duplicate_continuations.append(
                    DuplicateContinuationIssue(
                        first_part_id=aid,
                        first_part_page=candidate.page,
                        continues_on_page=candidate.continues_on_page,
                        conflicting_id=other_id,
                    )
                )

    ok = not issues and not duplicate_continuations
    return RankingValidationResult(ok=ok, issues=tuple(issues), duplicate_continuations=tuple(duplicate_continuations))


def _to_ranked_articles(parsed: dict, corpus_by_id: dict[str, CandidateArticle]) -> list[RankedArticle]:
    out = []
    for entry in parsed.get("ranked") or []:
        aid = entry.get("article_id")
        candidate = corpus_by_id.get(aid)
        out.append(
            RankedArticle(
                article_id=aid,
                page=candidate.page if candidate else -1,
                headline=candidate.headline if candidate else "",
                rank=entry.get("rank"),
                importance_score=entry.get("importance_score"),
                category=entry.get("category"),
                why_it_matters=entry.get("why_it_matters", ""),
                exclusion_risk=entry.get("exclusion_risk"),
            )
        )
    out.sort(key=lambda r: r.rank if isinstance(r.rank, int) else 999)
    return out


def _to_excluded_articles(parsed: dict, corpus_by_id: dict[str, CandidateArticle]) -> list[ExcludedArticle]:
    out = []
    for entry in parsed.get("excluded") or []:
        aid = entry.get("article_id")
        candidate = corpus_by_id.get(aid)
        out.append(
            ExcludedArticle(
                article_id=aid,
                page=candidate.page if candidate else -1,
                headline=candidate.headline if candidate else "",
                reason_code=entry.get("reason_code"),
                note=entry.get("note", ""),
            )
        )
    return out


def rank_edition(
    config: Config,
    edition: str,
    date: str,
    use_cache: bool = True,
    tracer: RunTracer | None = None,
) -> RankingOutcome:
    corpus = build_corpus(config, edition, date)
    corpus_by_id = {c.article_id: c for c in corpus}

    parsed, usage = _call_gemini_for_ranking(corpus, config, use_cache, tracer)
    validation = _validate(parsed, corpus_by_id, config.ranking.top_n)

    retried = False
    if not validation.ok:
        # Retry once with a corrective addendum describing exactly what was
        # wrong - temperature=0 means a blind retry would likely reproduce
        # the identical error, so the retry has to actually say what to fix.
        correction = "Your previous response had these problems - fix them:\n" + "\n".join(
            f"- {issue}" for issue in validation.issues
        )
        if validation.duplicate_continuations:
            correction += "\n" + "\n".join(
                f"- article {d.first_part_id!r} (continues on page {d.continues_on_page}) and "
                f"{d.conflicting_id!r} (on that same page) are the same story ranked twice - "
                f"keep only {d.first_part_id!r}"
                for d in validation.duplicate_continuations
            )
        parsed, usage = _call_gemini_for_ranking(corpus, config, use_cache=False, tracer=tracer, extra_instruction=correction)
        validation = _validate(parsed, corpus_by_id, config.ranking.top_n)
        retried = True

    # A separate "validation" stage, page_num=0 sentinel for "whole
    # edition" - mirrors grouping.py's gemini_call+validation pairing for
    # the per-page pipeline, so the Pipeline dashboard's existing
    # per-stage views work the same way for a ranking run.
    stage_ctx = tracer.stage(0, "validation") if tracer is not None else nullcontext({})
    with stage_ctx as detail:
        detail["ok"] = validation.ok
        detail["issues"] = list(validation.issues)
        detail["duplicate_continuations"] = [d.to_dict() for d in validation.duplicate_continuations]
        detail["excluded"] = [
            {"article_id": e.get("article_id"), "reason_code": e.get("reason_code"), "note": e.get("note")}
            for e in (parsed.get("excluded") or [])
        ]

    ranked = _to_ranked_articles(parsed, corpus_by_id)
    excluded = _to_excluded_articles(parsed, corpus_by_id)

    eligible_count_note = None
    if len(ranked) < config.ranking.top_n:
        eligible_count_note = (
            f"model returned {len(ranked)} articles, fewer than the requested {config.ranking.top_n} - "
            f"reported per spec rather than treated as a failure"
        )

    return RankingOutcome(
        ranked=ranked,
        excluded=excluded,
        validation=validation,
        usage=usage,
        retried=retried,
        eligible_count_note=eligible_count_note,
    )


def ranking_path(config: Config, edition: str, date: str) -> Path:
    return config.gold_root / edition / date / "ranking.json"


def process_edition_ranking(
    config: Config,
    edition: str,
    date: str,
    use_cache: bool = True,
    tracer: RunTracer | None = None,
) -> RankingOutcome:
    """Runs rank_edition and persists the result as this edition's current
    ranking (gold_root/{edition}/{date}/ranking.json) - separate from the
    per-corpus-hash response cache (which only avoids re-calling Gemini for
    an unchanged corpus+prompt), the same two-tier pattern
    articles_pipeline.py already uses for per-page Phase 2 output."""
    outcome = rank_edition(config, edition, date, use_cache=use_cache, tracer=tracer)

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "top_n": config.ranking.top_n,
        "ranked": [r.to_dict() for r in outcome.ranked],
        "excluded": [e.to_dict() for e in outcome.excluded],
        "validation_ok": outcome.validation.ok,
        "validation_issues": list(outcome.validation.issues),
        "duplicate_continuations": [d.to_dict() for d in outcome.validation.duplicate_continuations],
        "retried": outcome.retried,
        "eligible_count_note": outcome.eligible_count_note,
        "total_tokens": outcome.total_tokens,
        "all_cached": outcome.all_cached,
    }
    path = ranking_path(config, edition, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcome


def read_ranking(config: Config, edition: str, date: str) -> dict | None:
    path = ranking_path(config, edition, date)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
