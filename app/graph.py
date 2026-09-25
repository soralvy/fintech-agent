"""The compiled answering graph for ``POST /v1/query`` (Milestone 6).

One LangGraph ``StateGraph`` with nine nodes and three conditional edges
(docs/DECISIONS.md sections 9-11):

    START -> validate_query -> embed_query -> retrieve
    retrieve --route_tools--> decide_tool | build_context
    decide_tool --route_plan--> call_tool | build_context
    call_tool -> build_context
    build_context --route_context--> answer | finalize_insufficient
    answer -> finalize -> END
    finalize_insufficient -> END

No edge returns to an earlier node, so every run is bounded: at most one
planner call and at most one market-data call. Tools are reachable only when
the request sets ``use_tools`` and both a planner and a caller were given;
otherwise the same topology serves RAG only. A planning or tool failure is
optional evidence that is simply missing: it is recorded as a closed code,
never enters the context, and the query continues with document evidence.

With no evidence the model is never called. The model's answer is untrusted:
``finalize`` runs the pure rules of ``app.citations`` and builds every public
citation from the trusted retrieved chunks and the validated tool result.

Every node is wrapped to emit ``graph.node.started`` and
``graph.node.completed``, or ``graph.failed`` once when it raises; the
exception then propagates unchanged through ``ainvoke``. Events carry only the
safe fields of docs/DECISIONS.md section 19 -- never the question, chunk text,
the prompt, the answer, a planned symbol, or tool data.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.citations import (
    INSUFFICIENT_CONTEXT_ANSWER,
    Citation,
    ContextItem,
    FinalizedAnswer,
    QueryStatus,
    SourceItem,
    ToolContextItem,
    build_context_items,
    build_tool_context_item,
    finalize_answer,
)
from app.errors import AppError, InvalidQueryError, classify_error
from app.logging import log_event
from app.mcp_client import (
    ALLOWED_TOOLS,
    MarketToolName,
    ToolErrorCode,
    ToolFailure,
    ToolSuccess,
)
from app.openai_provider import (
    AnswerGenerator,
    GroundedAnswer,
    ToolPlan,
    ToolPlanner,
    ToolPlanningError,
)
from app.prompts import (
    GROUNDED_ANSWER_INSTRUCTIONS,
    TOOL_PLANNER_INSTRUCTIONS,
    render_grounded_answer_input,
    render_tool_plan_input,
)
from app.retrieval import RetrievedChunk
from app.symbols import InvalidSymbolError, normalize_symbol

logger = logging.getLogger(__name__)

QUESTION_MIN_CHARS = 3
QUESTION_MAX_CHARS = 2000

# A returned citation ID is logged verbatim only in this shape; anything else
# is logged as null and malformed, so a model cannot write into the logs.
_LOGGABLE_ID = re.compile(r"[A-Za-z][0-9]{1,4}")

type ToolRoute = Literal["decide_tool", "build_context"]
type PlanRoute = Literal["call_tool", "build_context"]
type ContextRoute = Literal["answer", "finalize_insufficient"]

type PlanRejectionReason = Literal[
    "incomplete_plan", "tool_not_allowed", "invalid_symbol"
]
# The closed set of non-fatal tool-path failures: a planning failure, a
# rejected plan, or one of the client's codes. Never text.
type ToolPathError = Literal["planning_failed"] | PlanRejectionReason | ToolErrorCode


class QueryRetriever(Protocol):
    """The two retrieval steps the graph runs, matching ``app.retrieval.Retriever``.

    A Protocol, so graph tests can run with a fake and without PostgreSQL.
    """

    async def embed_query(self, question: str) -> list[float]: ...

    async def retrieve(
        self, query_embedding: Sequence[float]
    ) -> list[RetrievedChunk]: ...


class MarketTools(Protocol):
    """The market-data caller, matching ``app.mcp_client.MarketDataTools``.

    ``call`` never raises for an expected failure: it returns a closed
    ``ToolFailure`` instead.
    """

    async def call(
        self, tool_name: str, arguments: Mapping[str, object]
    ) -> ToolSuccess | ToolFailure: ...


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """An approved plan: an allowed tool and a canonical symbol."""

    tool: MarketToolName
    symbol: str


@dataclass(frozen=True, slots=True)
class PlanRejection:
    """Why a plan was refused. Carries only the closed reason, never the plan."""

    reason: PlanRejectionReason


class QueryState(TypedDict, total=False):
    """The realized graph state (docs/DECISIONS.md section 9)."""

    question: str
    use_tools: bool
    query_embedding: list[float]
    retrieved_chunks: list[RetrievedChunk]
    tool_plan: ToolRequest | None
    tool_result: ToolSuccess | None
    tool_error: ToolPathError | None
    context_items: list[ContextItem]
    tool_context: ToolContextItem | None
    citation_map: dict[str, SourceItem]
    model_answer: GroundedAnswer
    answer: str
    citation_ids: list[str]
    citations: list[Citation]
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
    citations: tuple[Citation, ...]
    tools_used: tuple[MarketToolName, ...] = ()


def build_query_graph(
    *,
    retriever: QueryRetriever,
    answerer: AnswerGenerator,
    planner: ToolPlanner | None = None,
    market_tools: MarketTools | None = None,
) -> QueryGraph:
    """Compile the graph over the given retriever, answerer, and optional tools.

    Tools are available only when both ``planner`` and ``market_tools`` are
    given. The topology is the same either way; without tools, ``route_tools``
    never selects ``decide_tool``.
    """
    tools_available = planner is not None and market_tools is not None

    async def embed_query(state: QueryState) -> QueryState:
        return {"query_embedding": await retriever.embed_query(state["question"])}

    async def retrieve(state: QueryState) -> QueryState:
        return {"retrieved_chunks": await retriever.retrieve(state["query_embedding"])}

    def route_tools(state: QueryState) -> ToolRoute:
        return _route_tools(state, tools_available=tools_available)

    async def answer(state: QueryState) -> QueryState:
        prompt = render_grounded_answer_input(
            state["question"], state["context_items"], state["tool_context"]
        )
        model_answer = await answerer.generate_answer(
            instructions=GROUNDED_ANSWER_INSTRUCTIONS, prompt=prompt
        )
        return {"model_answer": model_answer}

    decide_tool, call_tool = _tool_nodes(planner, market_tools)
    nodes: dict[str, _Node] = {
        "validate_query": _validate_query,
        "embed_query": embed_query,
        "retrieve": retrieve,
        "decide_tool": decide_tool,
        "call_tool": call_tool,
        "build_context": _build_context,
        "answer": answer,
        "finalize": _finalize,
        "finalize_insufficient": _finalize_insufficient,
    }
    builder = StateGraph(QueryState)
    for name, node in nodes.items():
        builder.add_node(name, _logged(name, node))

    tool_paths: dict[Hashable, str] = {
        "decide_tool": "decide_tool",
        "build_context": "build_context",
    }
    plan_paths: dict[Hashable, str] = {
        "call_tool": "call_tool",
        "build_context": "build_context",
    }
    context_paths: dict[Hashable, str] = {
        "answer": "answer",
        "finalize_insufficient": "finalize_insufficient",
    }
    builder.add_edge(START, "validate_query")
    builder.add_edge("validate_query", "embed_query")
    builder.add_edge("embed_query", "retrieve")
    builder.add_conditional_edges("retrieve", route_tools, tool_paths)
    builder.add_conditional_edges("decide_tool", _route_plan, plan_paths)
    builder.add_edge("call_tool", "build_context")
    builder.add_conditional_edges("build_context", _route_context, context_paths)
    builder.add_edge("answer", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("finalize_insufficient", END)
    return builder.compile()


async def run_query(
    graph: QueryGraph, *, question: str, use_tools: bool
) -> QueryResult:
    """Run one query through ``graph`` and return its validated result.

    Any node's exception propagates unchanged; the failing node has already
    logged ``graph.failed``. The tool fields are read with ``get`` so a final
    state without them still yields a result.
    """
    started = time.monotonic()
    log_event(logger, "graph.started", use_tools=use_tools)
    final = await graph.ainvoke({"question": question, "use_tools": use_tools})
    tool_result: ToolSuccess | None = final.get("tool_result")
    tool_error: ToolPathError | None = final.get("tool_error")
    result = QueryResult(
        status=final["status"],
        answer=final["answer"],
        citations=tuple(final["citations"]),
        tools_used=derive_tools_used(tool_result),
    )
    log_event(
        logger,
        "graph.completed",
        status=result.status,
        citation_count=len(result.citations),
        tool_used=None if tool_result is None else tool_result.tool,
        tool_error=tool_error,
        duration_ms=_elapsed_ms(started),
    )
    return result


def approve_tool_plan(plan: ToolPlan) -> ToolRequest | PlanRejection | None:
    """Apply the application's rule to an untrusted plan (DECISIONS section 10.4).

    ``None`` means no tool. The tool must be allow-listed and both fields
    present together, and the symbol must pass the canonical rule, which also
    normalizes it. A rejection keeps only its closed reason.
    """
    tool_name, symbol = plan.tool_name, plan.symbol
    if tool_name is None and symbol is None:
        return None
    if tool_name is None or symbol is None:
        return PlanRejection("incomplete_plan")
    if tool_name not in ALLOWED_TOOLS:
        return PlanRejection("tool_not_allowed")
    try:
        normalized = normalize_symbol(symbol)
    except InvalidSymbolError:
        return PlanRejection("invalid_symbol")
    return ToolRequest(tool=tool_name, symbol=normalized)


def derive_tools_used(tool_result: ToolSuccess | None) -> tuple[MarketToolName, ...]:
    """The tools that supplied validated evidence: the successful call, if any.

    Independent of the final status and citations: an uncited or insufficient
    result still reports the call.
    """
    return () if tool_result is None else (tool_result.tool,)


# ---------------------------------------------------------------------------
# The optional tool path
# ---------------------------------------------------------------------------


def _tool_nodes(
    planner: ToolPlanner | None, market_tools: MarketTools | None
) -> tuple[_Node, _Node]:
    """Build ``decide_tool`` and ``call_tool``, or unreachable stand-ins."""
    if planner is None or market_tools is None:
        return _unavailable_tool_node, _unavailable_tool_node

    async def decide_tool(state: QueryState) -> QueryState:
        # The planner sees the question only, never retrieved text.
        try:
            plan = await planner.plan_tool(
                instructions=TOOL_PLANNER_INSTRUCTIONS,
                prompt=render_tool_plan_input(state["question"]),
            )
        except ToolPlanningError:
            return {"tool_plan": None, "tool_error": "planning_failed"}
        approved = approve_tool_plan(plan)
        if isinstance(approved, PlanRejection):
            log_event(
                logger,
                "planning.rejected",
                level=logging.WARNING,
                reason=approved.reason,
            )
            return {"tool_plan": None, "tool_error": approved.reason}
        return {"tool_plan": approved, "tool_error": None}

    async def call_tool(state: QueryState) -> QueryState:
        plan = state["tool_plan"]
        if plan is None:
            raise RuntimeError("call_tool ran without an approved plan")
        outcome = await market_tools.call(plan.tool, {"symbol": plan.symbol})
        if isinstance(outcome, ToolFailure):
            return {"tool_error": outcome.error_code}
        if outcome.tool != plan.tool:
            return {"tool_error": "malformed_provider_response"}
        return {"tool_result": outcome}

    return decide_tool, call_tool


async def _unavailable_tool_node(state: QueryState) -> QueryState:
    # ``route_tools`` never selects the tool path without tools.
    raise RuntimeError("the tool path is unavailable")


def _route_tools(state: QueryState, *, tools_available: bool) -> ToolRoute:
    route: ToolRoute = (
        "decide_tool" if tools_available and state["use_tools"] else "build_context"
    )
    log_event(
        logger,
        "graph.route",
        node="retrieve",
        route=route,
        tools_available=tools_available,
    )
    return route


def _route_plan(state: QueryState) -> PlanRoute:
    route: PlanRoute = (
        "call_tool" if state["tool_plan"] is not None else "build_context"
    )
    log_event(logger, "graph.route", node="decide_tool", route=route)
    return route


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
        "tool_plan": None,
        "tool_result": None,
        "tool_error": None,
        "context_items": [],
        "tool_context": None,
        "citation_map": {},
        "citation_ids": [],
        "citations": [],
    }


async def _build_context(state: QueryState) -> QueryState:
    # Reads only the validated result; ``tool_error`` never reaches context.
    items = build_context_items(state["retrieved_chunks"])
    tool_result = state["tool_result"]
    tool_item = (
        None if tool_result is None else build_tool_context_item(tool_result.result)
    )
    citation_map: dict[str, SourceItem] = {item.label: item for item in items}
    if tool_item is not None:
        citation_map[tool_item.label] = tool_item
    return {
        "context_items": items,
        "tool_context": tool_item,
        "citation_map": citation_map,
    }


def _route_context(state: QueryState) -> ContextRoute:
    context_count = _context_count(state)
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
    result = finalize_answer(
        question=state["question"],
        answer=model_answer.answer,
        citation_ids=model_answer.citation_ids,
        insufficient_context=model_answer.insufficient_context,
        items=state["context_items"],
        tool_item=state["tool_context"],
    )
    _log_citation_outcome(result, _context_count(state), len(model_answer.citation_ids))
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


def _context_count(state: QueryState) -> int:
    """The known labels: every ``D`` item, plus one when ``T1`` exists."""
    return len(state["context_items"]) + (0 if state["tool_context"] is None else 1)


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
