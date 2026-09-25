"""Request-local context labels and application-owned citations.

Pure code: no I/O, no logging, no framework imports (docs/DECISIONS.md
section 4). The answer model sees only the ephemeral labels ``D1…Dn`` and
``T1``; every public citation field comes from trusted data: a document
citation, including its excerpt, from the ``RetrievedChunk``, and an MCP
citation from the validated tool result, as structured ``fields`` rather than
prose. ``finalize_answer`` applies the finalize procedure of
docs/DECISIONS.md section 10.9, and ``make_excerpt`` the excerpt policy of
section 15. The graph's ``finalize`` node runs them and logs the outcome.

Only the market-data result models and ``QUOTE_FRESHNESS`` are imported from
``app.market_data``; the provider adapter is never used here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.market_data import QUOTE_FRESHNESS, CompanyOverview, MarketQuote
from app.retrieval import RetrievedChunk

EXCERPT_MAX_CHARS = 400

# The one request-local label a successful tool result can have.
TOOL_LABEL = "T1"

# A fixed description of the overview's refresh policy, written from the
# documented Alpha Vantage statement (docs/TECH_BASELINE.md section 3.18). It
# stands in for ``as_of`` when the provider supplies no ``latest_quarter``, and
# is never derived from provider data.
OVERVIEW_FRESHNESS = (
    "Provider company overview; refreshed when the company reports results"
)

MarketToolLabel = Literal["get_market_quote", "get_company_overview"]

INSUFFICIENT_CONTEXT_ANSWER = (
    "I do not have enough evidence in the ingested documents or available tool "
    "data to answer that question."
)

QueryStatus = Literal["answered", "insufficient_context"]
FailureReason = Literal["no_valid_citations", "blank_answer"]

# Any bracketed D- or T-number is a citation marker, including [D0], [D01],
# and [T2].
_MARKER = re.compile(r"\[([DT][0-9]+)\]")
_MARKER_GROUP = re.compile(
    r"[ \t]*\(\s*\[[DT][0-9]+\](?:\s*[,;]\s*\[[DT][0-9]+\])*\s*\)"
)
_MARKER_WITH_SPACE = re.compile(r"[ \t]*\[([DT][0-9]+)\]")
_LEADING_SPACE = re.compile(r"[ \t]*")
_CANONICAL_LABEL = re.compile(r"[DT][1-9][0-9]*")

# A sentence ends after . ! or ? followed by whitespace, and at every newline.
_SENTENCE_BOUNDARY = re.compile(r"[.!?](?=\s)|\n")
_TRIMMED = re.compile(r"\S(?:.*\S)?", re.DOTALL)
_WORD = re.compile(r"\S+")
_TOKEN = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_CHARS = 3


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One request-local label and the trusted chunk it stands for."""

    label: str
    chunk: RetrievedChunk


@dataclass(frozen=True, slots=True)
class DocumentCitation:
    """A public document citation, built only from trusted chunk metadata."""

    id: str
    document_id: UUID
    chunk_id: UUID
    filename: str
    page: int | None
    excerpt: str
    source_type: Literal["document"] = "document"


@dataclass(frozen=True, slots=True)
class ToolContextItem:
    """The trusted ``T1`` context item, built only from a validated tool result.

    ``fields`` is the allow-listed ``(name, value)`` pairs in their fixed
    order. The prompt block and the public citation both render from this one
    item, so the model sees exactly the fields the citation shows.
    """

    label: str
    tool: MarketToolLabel
    provider: str
    symbol: str
    as_of: str
    freshness: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class McpCitation:
    """A public MCP citation, built only from the trusted tool item."""

    id: str
    tool: MarketToolLabel
    provider: str
    symbol: str
    as_of: str
    fields: tuple[tuple[str, str], ...]
    source_type: Literal["mcp"] = "mcp"


type Citation = DocumentCitation | McpCitation
type SourceItem = ContextItem | ToolContextItem


@dataclass(frozen=True, slots=True)
class FinalizedAnswer:
    """The validated result of one answer.

    ``unknown_ids`` lists each distinct returned ID that is not a known label,
    unsanitized, in first-occurrence order; the caller sanitizes before
    logging. ``failure_reason`` is set only when validation forced the
    insufficient result, not when the model itself chose it.
    """

    status: QueryStatus
    answer: str
    citations: tuple[Citation, ...]
    unknown_ids: tuple[str, ...]
    failure_reason: FailureReason | None


def build_context_items(chunks: Sequence[RetrievedChunk]) -> list[ContextItem]:
    """Label chunks ``D1…Dn`` in the order retrieval returned them."""
    return [
        ContextItem(label=f"D{index}", chunk=chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]


def build_tool_context_item(result: MarketQuote | CompanyOverview) -> ToolContextItem:
    """Label a validated tool result ``T1`` with its allow-listed fields.

    Values are copied verbatim, as strings, from the strict model, in a fixed
    order (docs/DECISIONS.md section 15). A quote's ``as_of`` is its
    ``latest_trading_day``. An overview's is its ``latest_quarter``, or the
    fixed ``OVERVIEW_FRESHNESS`` when the provider supplied none, and an
    overview field whose value is ``None`` is omitted.
    """
    if isinstance(result, MarketQuote):
        return ToolContextItem(
            label=TOOL_LABEL,
            tool="get_market_quote",
            provider=result.provider,
            symbol=result.symbol,
            as_of=result.latest_trading_day,
            freshness=QUOTE_FRESHNESS,
            fields=(
                ("price", result.price),
                ("previous_close", result.previous_close),
                ("change", result.change),
                ("change_percent", result.change_percent),
                ("volume", result.volume),
                ("latest_trading_day", result.latest_trading_day),
            ),
        )
    overview_fields: tuple[tuple[str, str | None], ...] = (
        ("name", result.name),
        ("description", result.description),
        ("exchange", result.exchange),
        ("currency", result.currency),
        ("sector", result.sector),
        ("industry", result.industry),
        ("market_capitalization", result.market_capitalization),
        ("latest_quarter", result.latest_quarter),
    )
    return ToolContextItem(
        label=TOOL_LABEL,
        tool="get_company_overview",
        provider=result.provider,
        symbol=result.symbol,
        as_of=result.latest_quarter or OVERVIEW_FRESHNESS,
        freshness=OVERVIEW_FRESHNESS,
        fields=tuple(
            (name, value) for name, value in overview_fields if value is not None
        ),
    )


def finalize_answer(
    *,
    question: str,
    answer: str,
    citation_ids: Sequence[str],
    insufficient_context: bool,
    items: Sequence[ContextItem],
    tool_item: ToolContextItem | None = None,
) -> FinalizedAnswer:
    """Validate the model's structured answer against the trusted context.

    Follows docs/DECISIONS.md section 10.9 step by step. Labels are matched
    exactly, with no case folding or trimming. ``T1`` is a known label only
    when ``tool_item`` is given. Every insufficient result carries the fixed
    answer text, never model text.
    """
    by_label: dict[str, SourceItem] = {item.label: item for item in items}
    if tool_item is not None:
        by_label[tool_item.label] = tool_item
    returned = list(dict.fromkeys(citation_ids))
    known = [label for label in returned if label in by_label]
    unknown = tuple(label for label in returned if label not in by_label)

    if insufficient_context:
        return _insufficient(unknown, None)

    citations: list[Citation] = []
    for label in known:
        source = by_label[label]
        if isinstance(source, ToolContextItem):
            citations.append(_mcp_citation(source))
            continue
        chunk = source.chunk
        excerpt = make_excerpt(chunk.content, question=question, answer=answer)
        if excerpt:
            citations.append(_citation(label, chunk, excerpt))
    if not citations:
        return _insufficient(unknown, "no_valid_citations")

    text = sanitize_markers(answer, {citation.id for citation in citations})
    if not text:
        return _insufficient(unknown, "blank_answer")

    return FinalizedAnswer(
        status="answered",
        answer=text,
        citations=tuple(citations),
        unknown_ids=unknown,
        failure_reason=None,
    )


def sanitize_markers(answer: str, final_labels: set[str]) -> str:
    """Keep only markers for final citation labels, then strip the answer.

    A marker is kept only when its label is canonical and final. Removal also
    deletes the horizontal whitespace before the marker; a parenthesised
    group of markers is rebuilt from its kept markers, or deleted whole when
    none is kept. Text that is not a ``[D<digits>]`` or ``[T<digits>]``
    marker is never changed.
    """

    def kept(label: str) -> bool:
        return _CANONICAL_LABEL.fullmatch(label) is not None and label in final_labels

    def rewrite_group(match: re.Match[str]) -> str:
        group = match.group(0)
        labels = _MARKER.findall(group)
        survivors = [label for label in labels if kept(label)]
        if len(survivors) == len(labels):
            return group
        if not survivors:
            return ""
        leading = _LEADING_SPACE.match(group)
        prefix = leading.group(0) if leading else ""
        return prefix + "(" + ", ".join(f"[{label}]" for label in survivors) + ")"

    def rewrite_marker(match: re.Match[str]) -> str:
        return match.group(0) if kept(match.group(1)) else ""

    text = _MARKER_GROUP.sub(rewrite_group, answer)
    text = _MARKER_WITH_SPACE.sub(rewrite_marker, text)
    return text.strip()


def make_excerpt(content: str, *, question: str, answer: str) -> str:
    """Return a bounded, exact substring of ``content`` that best supports the answer.

    Implements docs/DECISIONS.md section 15: the sentence sharing the most
    distinct query tokens with the question and answer (earliest on a tie),
    narrowed to a word-boundary window of at most ``EXCERPT_MAX_CHARS`` when
    it is longer; with no overlap, the leading word-boundary window. Returns
    ``""`` for blank content, and never generates text.
    """
    spans = _sentence_spans(content)
    if not spans:
        return ""
    query = _tokens(question) | _tokens(_MARKER.sub("", answer))

    best_span, best_score = spans[0], 0
    for span in spans:
        score = _score(content, span, query)
        if score > best_score:
            best_span, best_score = span, score

    if best_score == 0:
        start, end = next(_word_windows(content, spans[0][0], len(content)))
        return content[start:end]

    start, end = best_span
    if end - start <= EXCERPT_MAX_CHARS:
        return content[start:end]

    windows = _word_windows(content, start, end)
    best_window = next(windows)
    best_window_score = _score(content, best_window, query)
    for window in windows:
        score = _score(content, window, query)
        if score > best_window_score:
            best_window, best_window_score = window, score
    return content[best_window[0] : best_window[1]]


def _insufficient(
    unknown_ids: tuple[str, ...], failure_reason: FailureReason | None
) -> FinalizedAnswer:
    return FinalizedAnswer(
        status="insufficient_context",
        answer=INSUFFICIENT_CONTEXT_ANSWER,
        citations=(),
        unknown_ids=unknown_ids,
        failure_reason=failure_reason,
    )


def _citation(label: str, chunk: RetrievedChunk, excerpt: str) -> DocumentCitation:
    return DocumentCitation(
        id=label,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        filename=chunk.filename,
        page=chunk.page_number,
        excerpt=excerpt,
    )


def _mcp_citation(item: ToolContextItem) -> McpCitation:
    return McpCitation(
        id=item.label,
        tool=item.tool,
        provider=item.provider,
        symbol=item.symbol,
        as_of=item.as_of,
        fields=item.fields,
    )


def _sentence_spans(content: str) -> list[tuple[int, int]]:
    """Whitespace-trimmed, non-empty sentence spans as offsets into ``content``."""
    ends = [match.end() for match in _SENTENCE_BOUNDARY.finditer(content)]
    spans: list[tuple[int, int]] = []
    start = 0
    for end in [*ends, len(content)]:
        trimmed = _TRIMMED.search(content, start, end)
        if trimmed is not None:
            spans.append(trimmed.span())
        start = end
    return spans


def _word_windows(content: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    """Yield, in order, every window that starts at a word within ``[start, end)``
    and holds as many whole words as fit in ``EXCERPT_MAX_CHARS``.

    A single word longer than that yields its first ``EXCERPT_MAX_CHARS``
    characters. The range always holds a word, since callers pass a
    whitespace-trimmed, non-empty span.
    """
    words = [match.span() for match in _WORD.finditer(content, start, end)]
    for index, (word_start, word_end) in enumerate(words):
        limit = word_start + EXCERPT_MAX_CHARS
        if word_end > limit:
            yield word_start, limit
            continue
        window_end = word_end
        for _, next_end in words[index + 1 :]:
            if next_end > limit:
                break
            window_end = next_end
        yield word_start, window_end


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(text.casefold())
        if len(token) >= _MIN_TOKEN_CHARS
    }


def _score(content: str, span: tuple[int, int], query: set[str]) -> int:
    return len(query & _tokens(content[span[0] : span[1]]))
