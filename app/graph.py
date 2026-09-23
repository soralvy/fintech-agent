"""The compiled answering graph for ``POST /v1/query`` (Milestone 4, no tools).

One LangGraph ``StateGraph`` with seven nodes and a single conditional edge
(docs/DECISIONS.md sections 9-11):

    START -> validate_query -> embed_query -> retrieve -> build_context
    build_context --route_context--> answer -> finalize -> END
                                \\--> finalize_insufficient -> END

No edge returns to an earlier node, so every run is bounded. With no evidence
the model is never called. The model's answer is untrusted: ``finalize`` runs
the pure rules of ``app.citations`` and builds every public citation from the
trusted retrieved chunks.

Every node is wrapped to emit ``graph.node.started`` and
``graph.node.completed``, or ``graph.failed`` once when it raises; the
exception then propagates unchanged through ``ainvoke``. Events carry only the
safe fields of docs/DECISIONS.md section 19 -- never the question, chunk text,
the prompt, or the answer.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Hashable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.citations import (
    INSUFFICIENT_CONTEXT_ANSWER,
    ContextItem,
    DocumentCitation,
    FinalizedAnswer,
    QueryStatus,
    build_context_items,
    finalize_answer,
)
from app.errors import AppError, InvalidQueryError, classify_error
from app.logging import log_event
from app.openai_provider import AnswerGenerator, GroundedAnswer
from app.prompts import GROUNDED_ANSWER_INSTRUCTIONS, render_grounded_answer_input
from app.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

QUESTION_MIN_CHARS = 3
QUESTION_MAX_CHARS = 2000

# A returned citation ID is logged verbatim only in this shape; anything else
# is logged as null and malformed, so a model cannot write into the logs.
_LOGGABLE_ID = re.compile(r"[A-Za-z][0-9]{1,4}")

type ContextRoute = Literal["answer", "finalize_insufficient"]


class QueryRetriever(Protocol):
    """The two retrieval steps the graph runs, matching ``app.retrieval.Retriever``.

    A Protocol, so graph tests can run with a fake and without PostgreSQL.
    """

    async def embed_query(self, question: str) -> list[float]: ...

    async def retrieve(
        self, query_embedding: Sequence[float]
    ) -> list[RetrievedChunk]: ...


class QueryState(TypedDict, total=False):
    """The Milestone 4 subset of the graph state (docs/DECISIONS.md section 9)."""

    question: str
    use_tools: bool
    query_embedding: list[float]
    retrieved_chunks: list[RetrievedChunk]
    context_items: list[ContextItem]
    citation_map: dict[str, ContextItem]
    model_answer: GroundedAnswer
    answer: str
    citation_ids: list[str]
    citations: list[DocumentCitation]
    status: QueryStatus


type QueryGraph = CompiledStateGraph[QueryState, None, QueryState, QueryState]


class _Node(Protocol):
    """A graph node; the parameter name matches LangGraph's own node protocol."""

    def __call__(self, state: QueryState) -> Awaitable[QueryState]: ...


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The validated outcome of one query, ready for the HTTP schema."""

    status: QueryStatus
    answer: str
    citations: tuple[DocumentCitation, ...]


def build_query_graph(
    *, retriever: QueryRetriever, answerer: AnswerGenerator
) -> QueryGraph:
    """Compile the Milestone 4 graph over the given retriever and answerer."""

    async def embed_query(state: QueryState) -> QueryState:
        return {"query_embedding": await retriever.embed_query(state["question"])}

    async def retrieve(state: QueryState) -> QueryState:
        return {"retrieved_chunks": await retriever.retrieve(state["query_embedding"])}

    async def answer(state: QueryState) -> QueryState:
        prompt = render_grounded_answer_input(state["question"], state["context_items"])
        model_answer = await answerer.generate_answer(
            instructions=GROUNDED_ANSWER_INSTRUCTIONS, prompt=prompt
        )
        return {"model_answer": model_answer}

    nodes: dict[str, _Node] = {
        "validate_query": _validate_query,
        "embed_query": embed_query,
        "retrieve": retrieve,
        "build_context": _build_context,
        "answer": answer,
        "finalize": _finalize,
        "finalize_insufficient": _finalize_insufficient,
    }
    builder = StateGraph(QueryState)
    for name, node in nodes.items():
        builder.add_node(name, _logged(name, node))

    path_map: dict[Hashable, str] = {
        "answer": "answer",
        "finalize_insufficient": "finalize_insufficient",
    }
    builder.add_edge(START, "validate_query")
    builder.add_edge("validate_query", "embed_query")
    builder.add_edge("embed_query", "retrieve")
    builder.add_edge("retrieve", "build_context")
    builder.add_conditional_edges("build_context", _route_context, path_map)
    builder.add_edge("answer", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("finalize_insufficient", END)
    return builder.compile()


async def run_query(
    graph: QueryGraph, *, question: str, use_tools: bool
) -> QueryResult:
    """Run one query through ``graph`` and return its validated result.

    Any node's exception propagates unchanged; the failing node has already
    logged ``graph.failed``.
    """
    started = time.monotonic()
    log_event(logger, "graph.started", use_tools=use_tools)
    final = await graph.ainvoke({"question": question, "use_tools": use_tools})
    result = QueryResult(
        status=final["status"],
        answer=final["answer"],
        citations=tuple(final["citations"]),
    )
    log_event(
        logger,
        "graph.completed",
        status=result.status,
        citation_count=len(result.citations),
        duration_ms=_elapsed_ms(started),
    )
    return result


# ---------------------------------------------------------------------------
# Nodes that need no injected dependency
# ---------------------------------------------------------------------------


async def _validate_query(state: QueryState) -> QueryState:
    question = state["question"].strip()
    if not QUESTION_MIN_CHARS <= len(question) <= QUESTION_MAX_CHARS:
        raise InvalidQueryError
    return {
        "question": question,
        "use_tools": state.get("use_tools", False),
        "retrieved_chunks": [],
        "context_items": [],
        "citation_map": {},
        "citation_ids": [],
        "citations": [],
    }


async def _build_context(state: QueryState) -> QueryState:
    items = build_context_items(state["retrieved_chunks"])
    return {
        "context_items": items,
        "citation_map": {item.label: item for item in items},
    }


def _route_context(state: QueryState) -> ContextRoute:
    context_count = len(state["context_items"])
    route: ContextRoute = "answer" if context_count else "finalize_insufficient"
    log_event(
        logger,
        "graph.route",
        node="build_context",
        route=route,
        context_count=context_count,
    )
    return route


async def _finalize(state: QueryState) -> QueryState:
    model_answer = state["model_answer"]
    items = state["context_items"]
    result = finalize_answer(
        question=state["question"],
        answer=model_answer.answer,
        citation_ids=model_answer.citation_ids,
        insufficient_context=model_answer.insufficient_context,
        items=items,
    )
    _log_citation_outcome(result, len(items), len(model_answer.citation_ids))
    return {
        "answer": result.answer,
        "citation_ids": [citation.id for citation in result.citations],
        "citations": list(result.citations),
        "status": result.status,
    }


async def _finalize_insufficient(state: QueryState) -> QueryState:
    return {
        "answer": INSUFFICIENT_CONTEXT_ANSWER,
        "citation_ids": [],
        "citations": [],
        "status": "insufficient_context",
    }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _logged(name: str, node: _Node) -> _Node:
    """Wrap ``node`` with its started/completed events, or one ``graph.failed``."""

    async def run(state: QueryState) -> QueryState:
        started = time.monotonic()
        log_event(logger, "graph.node.started", node=name)
        try:
            update = await node(state)
        except Exception as exc:
            log_event(
                logger,
                "graph.failed",
                level=logging.ERROR,
                node=name,
                error_code=exc.code if isinstance(exc, AppError) else "internal_error",
                error_type=classify_error(exc),
                duration_ms=_elapsed_ms(started),
            )
            raise
        log_event(
            logger, "graph.node.completed", node=name, duration_ms=_elapsed_ms(started)
        )
        return update

    return run


def _log_citation_outcome(
    result: FinalizedAnswer, known_context_count: int, returned_count: int
) -> None:
    for returned_id in result.unknown_ids:
        loggable = _LOGGABLE_ID.fullmatch(returned_id) is not None
        log_event(
            logger,
            "citation.unknown_id",
            level=logging.WARNING,
            returned_id=returned_id if loggable else None,
            malformed=not loggable,
            known_context_count=known_context_count,
        )
    if result.failure_reason is not None:
        log_event(
            logger,
            "citation.validation_failed",
            level=logging.WARNING,
            reason=result.failure_reason,
            known_context_count=known_context_count,
            returned_count=returned_count,
        )


def _elapsed_ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)
