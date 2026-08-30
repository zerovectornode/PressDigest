"""Builds the edition-wide importance-ranking prompt and its response JSON
schema.

One call for the WHOLE edition, not per page: per-page ranking calls would
produce non-comparable scores (a weak page's best article would look
artificially important next to nothing to compete with) - ranking requires
seeing every candidate article together.

This is the first place in the pipeline where the model is asked to WRITE
prose (`why_it_matters`) rather than only report line-number facts about
text we already have. That's a deliberate, isolated exception - see
assemble.py's module docstring for the rule this breaks and ranking.py for
how the generated field is kept structurally separate from extracted
article text.
"""
from __future__ import annotations

CATEGORIES = (
    "POLITY_GOVERNANCE",
    "ECONOMY",
    "INTERNATIONAL",
    "ENVIRONMENT",
    "SCIENCE_TECH",
    "SOCIAL_ISSUES",
    "JUDICIARY",
    "SECURITY_DEFENCE",
    "AGRICULTURE",
    "HEALTH",
    "EDUCATION",
    "OTHER",
)

EXCLUSION_RISKS = ("none", "possible_opinion", "possible_promotional")

EXCLUSION_REASON_CODES = (
    "PROMOTIONAL",
    "OPINION_WITHOUT_ANALYSIS",
    "ENTERTAINMENT",
    "LOCAL_WITHOUT_BROADER_RELEVANCE",
    "ROUTINE_STATEMENT",
    "CONTINUATION_OF_EARLIER_ARTICLE",
    "BELOW_THRESHOLD",
    "OTHER",
)

# Not every rejected candidate gets an entry here - only ones that were
# genuinely close calls, capped well below the full ~107-article corpus, so
# this doesn't become bookkeeping load with no diagnostic benefit. See
# ranking.py / design/DESIGN.md for why this exists: without it, there was
# no way to tell whether a rejected article was excluded on merit or never
# seriously considered.
MAX_EXCLUDED_ENTRIES = 15


def _response_schema(top_n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "ranked": {
                "type": "array",
                "maxItems": top_n,
                "items": {
                    "type": "object",
                    "properties": {
                        "article_id": {"type": "string"},
                        "rank": {"type": "integer"},
                        "importance_score": {"type": "integer"},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "why_it_matters": {"type": "string"},
                        "exclusion_risk": {"type": "string", "enum": list(EXCLUSION_RISKS)},
                    },
                    "required": [
                        "article_id",
                        "rank",
                        "importance_score",
                        "category",
                        "why_it_matters",
                        "exclusion_risk",
                    ],
                },
            },
            "excluded": {
                "type": "array",
                # No maxItems and no enum on reason_code here, deliberately -
                # verified live that adding EITHER one (on top of "ranked"'s
                # own maxItems + two enums) makes Gemini's structured-output
                # schema validator reject the whole request with an opaque
                # 400 INVALID_ARGUMENT that names no specific field. Bisected
                # by elimination: neither enum content, property count, nor
                # required-list shape mattered on their own - only "does
                # this schema carry a second maxItems/enum" did. Reads as a
                # real aggregate complexity ceiling on this API, not
                # anything wrong with the schema's meaning. Both the 15-item
                # cap and the reason_code enum are still enforced - via the
                # prompt text and ranking.py's post-hoc _validate() check -
                # just not at the schema level.
                "items": {
                    "type": "object",
                    "properties": {
                        "article_id": {"type": "string"},
                        "reason_code": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["article_id", "reason_code", "note"],
                },
            },
        },
        "required": ["ranked", "excluded"],
    }


def response_schema(top_n: int) -> dict:
    return _response_schema(top_n)


def _system_prompt(top_n: int) -> str:
    categories_list = ", ".join(CATEGORIES)
    return f"""\
You are ranking every article extracted from one full issue of a print \
newspaper (The Hindu) by editorial importance. You are given a list of \
candidate articles from every page of the edition together - rank across \
the WHOLE edition, not page by page. Page position (which page an article \
appeared on) is weak evidence of importance and must NOT dominate your \
ranking; a page-11 article can matter more than a page-1 one.

Select the {top_n} most important articles based on:
- Significant national or international events
- Government policies and schemes
- Economic developments and data
- Social issues and environmental matters
- Science and technology advancements
- Important judicial or constitutional decisions
- Articles providing context, background, causes, impacts, and multiple perspectives
- Policy implications, governance challenges, international relations
- Editorials, analytical columns, and commentary that examine causes, consequences, \
policy trade-offs, or multiple perspectives on a significant issue. Judge these on \
analytical substance, not on the fact that they express a viewpoint.
- Long-form features examining causes, impacts, and multiple perspectives on a \
significant event are strong candidates, not soft news.

EXCLUDE:
- Advertisements, promotions, congratulatory messages
- Purely opinion-based pieces that assert a position without supporting analysis, \
evidence, or examination of consequences
- Entertainment news
- Local incidents without broader relevance
- Routine political statements without policy significance

Prefer analytical depth over mere event reporting - an article that explains \
causes, impacts, and multiple perspectives outranks one that only reports \
that an event happened.

EDITORIALS AND OPINION: an editorial arguing a position while examining evidence \
and policy implications IS eligible - do not exclude it just because it is on the \
opinion/editorial page or expresses a viewpoint. A column merely asserting a view, \
or a personal reflection without analytical content, is not eligible.

PROCEDURAL VS. SUBSTANTIVE: procedural steps in ongoing litigation or an \
administrative process (a court seeking a reply, a ministry scheduling a meeting) \
rank BELOW decided matters and announced policies with identifiable consequences - \
they may still be included if nothing more substantive is available, but should not \
outrank a matter that has actually been decided or announced.

DUPLICATE / CONTINUED ARTICLES: some stories are split across two entries - \
a first part (with the headline and deck) whose `continues_on_page` field \
names the page its continuation was found on, and that continuation itself \
registered as a separate entry (body text with no headline of its own) on \
that later page. When two entries are clearly the same story, rank ONLY \
the first part (the one with the headline and deck, lower page number) and \
ignore the continuation entirely - do not rank the same story twice, and \
do not rank a continuation instead of its first part.

For each article you select, return:
- article_id: copied exactly from the input
- rank: 1 (most important) through {top_n}
- importance_score: 0-100, consistent with rank ordering (higher rank number \
never has a higher score than a lower rank number)
- category: exactly one of: {categories_list}. Never invent a category \
outside this list.
- why_it_matters: at most 30 words, WRITTEN BY YOU. State the article's \
significance and implications - do not just restate or recap the headline.
- exclusion_risk: "none" if this article clearly belongs, "possible_opinion" \
if it leans opinion/commentary without strong analytical value, \
"possible_promotional" if it reads like a promotion or congratulatory \
notice you're including with lower confidence.

Return exactly {top_n} articles ranked 1 to {top_n}, unless fewer than \
{top_n} articles in the input are genuinely eligible under the selection \
and exclusion criteria above - in that case return only the eligible ones \
rather than padding the list.

ALSO return an `excluded` list: articles you seriously considered as \
plausible candidates for the top {top_n} but ultimately rejected, with a \
brief reason. Do NOT attempt this for all input articles - only ones that \
were genuinely close calls, up to {MAX_EXCLUDED_ENTRIES} entries. Most of \
the corpus (routine reports, ads, furniture) was never a real candidate \
and does not belong in this list. For each entry:
- article_id: copied exactly from the input
- reason_code: exactly one of: {", ".join(EXCLUSION_REASON_CODES)}
- note: at most 20 words explaining the specific reason
"""


SYSTEM_PROMPT = _system_prompt(20)


def build_system_prompt(top_n: int) -> str:
    return _system_prompt(top_n)


def build_user_prompt(articles: list[dict]) -> str:
    """`articles` is a list of plain dicts already shaped as:
    {article_id, page, headline, deck, continues_on_page, body_preview}
    - see ranking.py for how the corpus is built from gold JSON."""
    header = f"{len(articles)} candidate articles from this edition:\n"
    rows = []
    for a in articles:
        rows.append(
            "\n".join(
                [
                    f"--- article_id={a['article_id']} page={a['page']} continues_on_page={a['continues_on_page']} ---",
                    f"headline: {a['headline']}",
                    f"deck: {a['deck']}",
                    f"body_preview: {a['body_preview']}",
                ]
            )
        )
    return header + "\n\n".join(rows)
