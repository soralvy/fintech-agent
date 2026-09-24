"""Citation labels, finalize rules, marker sanitization, and excerpts.

Pure unit tests of ``app/citations.py`` (docs/DECISIONS.md sections 10.9 and
15), with no PostgreSQL, no model, and no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest

from app.citations import (
    EXCERPT_MAX_CHARS,
    INSUFFICIENT_CONTEXT_ANSWER,
    ContextItem,
    FinalizedAnswer,
    build_context_items,
    finalize_answer,
    make_excerpt,
    sanitize_markers,
)
from app.retrieval import RetrievedChunk

QUESTION = "Why did Acme's European revenue decline?"
ANSWER_ABOUT_DECLINE = "European revenue declined 4% on currency headwinds [D1]."

# About 800 tokens of filler that shares no query token with the questions and
# answers below, followed by the one supporting sentence.
FILLER = "The committee reviewed routine governance matters at a scheduled meeting."
END_SENTENCE = (
    "European revenue declined 4% year over year because of currency headwinds."
)
END_OF_CHUNK = " ".join([FILLER] * 43 + [END_SENTENCE])


def chunk(
    content: str = "Revenue grew.",
    *,
    filename: str = "acme.txt",
    page: int | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=filename,
        page_number=page,
        content=content,
        cosine_distance=0.2,
        similarity=0.8,
    )


def context(count: int) -> list[ContextItem]:
    return build_context_items(
        [chunk(f"Revenue fact number {index} is recorded.") for index in range(count)]
    )


def finalize(
    answer: str,
    citation_ids: Sequence[str],
    items: Sequence[ContextItem],
    *,
    insufficient_context: bool = False,
    question: str = "What is the revenue fact?",
) -> FinalizedAnswer:
    return finalize_answer(
        question=question,
        answer=answer,
        citation_ids=citation_ids,
        insufficient_context=insufficient_context,
        items=items,
    )


def assert_insufficient(result: FinalizedAnswer) -> None:
    assert result.status == "insufficient_context"
    assert result.answer == INSUFFICIENT_CONTEXT_ANSWER
    assert result.citations == ()


def cited(result: FinalizedAnswer) -> list[str]:
    return [citation.id for citation in result.citations]


# ---------------------------------------------------------------------------
# Context labels
# ---------------------------------------------------------------------------


def test_labels_follow_retrieval_order() -> None:
    chunks = [chunk("first."), chunk("second."), chunk("third.")]

    items = build_context_items(chunks)

    assert [item.label for item in items] == ["D1", "D2", "D3"]
    assert [item.chunk for item in items] == chunks


def test_no_chunks_give_no_labels() -> None:
    assert build_context_items([]) == []


# ---------------------------------------------------------------------------
# finalize_answer: ID validation and the model's flag (AC3)
# ---------------------------------------------------------------------------


def test_duplicate_ids_are_deduplicated_in_first_occurrence_order() -> None:
    result = finalize("Facts [D2] and [D1].", ["D2", "D1", "D2", "D1"], context(2))

    assert result.status == "answered"
    assert cited(result) == ["D2", "D1"]
    assert result.unknown_ids == ()
    assert result.failure_reason is None


@pytest.mark.parametrize("returned", ["D9", "d1", " D1", "D1 ", "D01", "D0"])
def test_ids_are_known_only_on_an_exact_match(returned: str) -> None:
    result = finalize("Fact [D1].", ["D1", returned], context(2))

    assert cited(result) == ["D1"]
    assert result.unknown_ids == (returned,)


def test_each_distinct_unknown_id_is_reported_once() -> None:
    result = finalize("Fact [D1].", ["D7", "D1", "D8", "D7"], context(1))

    assert result.unknown_ids == ("D7", "D8")


def test_all_unknown_ids_fail_closed_to_no_valid_citations() -> None:
    result = finalize("Revenue grew [D9].", ["D9", "d1"], context(2))

    assert_insufficient(result)
    assert result.failure_reason == "no_valid_citations"
    assert result.unknown_ids == ("D9", "d1")


def test_no_ids_fail_closed_to_no_valid_citations() -> None:
    result = finalize("Revenue grew.", [], context(2))

    assert_insufficient(result)
    assert result.failure_reason == "no_valid_citations"


def test_the_models_insufficient_flag_is_honored_even_with_valid_ids() -> None:
    result = finalize(
        "Model prose that must not appear [D1].",
        ["D1", "D9"],
        context(2),
        insufficient_context=True,
    )

    assert_insufficient(result)
    assert result.failure_reason is None
    assert result.unknown_ids == ("D9",)


def test_citations_follow_the_final_id_order_not_the_label_order() -> None:
    result = finalize("B [D3], then A [D1].", ["D3", "D1"], context(3))

    assert cited(result) == ["D3", "D1"]


def test_a_valid_id_without_a_marker_is_still_cited() -> None:
    result = finalize("Revenue grew.", ["D1"], context(1))

    assert result.answer == "Revenue grew."
    assert cited(result) == ["D1"]


# ---------------------------------------------------------------------------
# Marker rule (AC3; D7, D18)
# ---------------------------------------------------------------------------


def test_valid_markers_are_kept_including_a_two_digit_context() -> None:
    result = finalize("A [D1] and B [D9].", ["D1", "D9"], context(9))

    assert result.answer == "A [D1] and B [D9]."


@pytest.mark.parametrize(
    ("answer", "citation_ids", "expected"),
    [
        ("Fell [D1] and [D9].", ["D1"], "Fell [D1] and."),
        ("Fell [D1] and [D0].", ["D1"], "Fell [D1] and."),
        ("Fell [D1] and [D01].", ["D1", "D01"], "Fell [D1] and."),
        ("Fell [D1] and [D2].", ["D1"], "Fell [D1] and."),
        ("Fell [D1], again [D1].", ["D1"], "Fell [D1], again [D1]."),
        ("See [Q1] and [A1] [D1].", ["D1"], "See [Q1] and [A1] [D1]."),
        ("Plain D9 and D1 stay [D1].", ["D1"], "Plain D9 and D1 stay [D1]."),
    ],
    ids=[
        "unknown-removed",
        "malformed-removed",
        "non-canonical-removed-even-if-returned",
        "uncited-known-removed",
        "duplicates-kept",
        "other-brackets-unchanged",
        "plain-labels-unchanged",
    ],
)
def test_marker_rule(answer: str, citation_ids: list[str], expected: str) -> None:
    result = finalize(answer, citation_ids, context(2))

    assert result.status == "answered"
    assert result.answer == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("declined [D9].", "declined."),
        ("fell ([D9]).", "fell."),
        ("fell ([D1], [D9]).", "fell ([D1])."),
        ("fell ([D9]; [D1]).", "fell ([D1])."),
        ("fell ( [D1] ;[D2] ).", "fell ( [D1] ;[D2] )."),
        ("[D9] Revenue fell [D1].", "Revenue fell [D1]."),
        ("fell\t[D9].", "fell."),
        ("  fell [D1].  ", "fell [D1]."),
    ],
)
def test_removal_artifacts_are_cleaned(answer: str, expected: str) -> None:
    assert sanitize_markers(answer, {"D1", "D2"}) == expected


def test_newlines_before_a_removed_marker_are_kept() -> None:
    assert sanitize_markers("Revenue fell.\n[D9] Costs rose [D1].", {"D1"}) == (
        "Revenue fell.\n Costs rose [D1]."
    )


def test_a_non_canonical_label_is_removed_even_if_final() -> None:
    assert sanitize_markers("Fell [D01].", {"D01"}) == "Fell."


def test_an_answer_of_only_removed_markers_is_blank() -> None:
    result = finalize("[D9] [D01]", ["D1", "D01"], context(1))

    assert_insufficient(result)
    assert result.failure_reason == "blank_answer"


def test_a_blank_model_answer_is_rejected() -> None:
    result = finalize("   ", ["D1"], context(1))

    assert_insufficient(result)
    assert result.failure_reason == "blank_answer"


def test_a_marker_for_a_dropped_citation_is_removed() -> None:
    items = build_context_items([chunk("Revenue grew."), chunk("\n\t\n")])

    result = finalize("Grew [D1] [D2].", ["D1", "D2"], items)

    assert cited(result) == ["D1"]
    assert result.answer == "Grew [D1]."


# ---------------------------------------------------------------------------
# Trusted citation fields (AC4)
# ---------------------------------------------------------------------------


def test_citation_fields_come_only_from_the_trusted_chunk() -> None:
    pdf = chunk("Margins improved in 2025.", filename="acme.pdf", page=18)
    txt = chunk("Revenue fell in Europe.", filename="notes.md")
    items = build_context_items([pdf, txt])

    result = finalize("Margins improved [D1]; revenue fell [D2].", ["D1", "D2"], items)

    first, second = result.citations
    assert (first.id, first.source_type) == ("D1", "document")
    assert (first.document_id, first.chunk_id) == (pdf.document_id, pdf.chunk_id)
    assert (first.filename, first.page) == ("acme.pdf", 18)
    assert first.excerpt == pdf.content
    assert (second.id, second.filename, second.page) == ("D2", "notes.md", None)
    assert (second.document_id, second.chunk_id) == (txt.document_id, txt.chunk_id)


@pytest.mark.parametrize(
    "content", ["\n\t\n", "     "], ids=["tabs-newlines", "spaces"]
)
def test_a_whitespace_only_chunk_drops_its_citation(content: str) -> None:
    """Such content passes the database CHECK (tabs and newlines) or never
    reaches it (spaces), but ingestion never stores either, so the chunk is
    built directly."""
    items = build_context_items([chunk(content)])

    result = finalize("Revenue grew [D1].", ["D1"], items)

    assert_insufficient(result)
    assert result.failure_reason == "no_valid_citations"


def test_the_excerpt_uses_the_question_and_answer() -> None:
    items = build_context_items(
        [chunk("Headcount was flat. Margins improved. Revenue fell in Europe.")]
    )

    result = finalize(
        "Revenue fell in Europe [D1].",
        ["D1"],
        items,
        question="What happened to European sales?",
    )

    assert result.citations[0].excerpt == "Revenue fell in Europe."


# ---------------------------------------------------------------------------
# make_excerpt (AC4; D23)
# ---------------------------------------------------------------------------


def assert_bounded_substring(excerpt: str, content: str) -> None:
    assert excerpt
    assert excerpt in content
    assert len(excerpt) <= EXCERPT_MAX_CHARS


def test_support_near_the_beginning_selects_the_first_sentence() -> None:
    content = "Revenue in Europe declined 4%. Headcount was flat. Margins improved."

    excerpt = make_excerpt(content, question=QUESTION, answer=ANSWER_ABOUT_DECLINE)

    assert excerpt == "Revenue in Europe declined 4%."


def test_support_near_the_end_of_a_long_chunk_is_found() -> None:
    assert len(END_OF_CHUNK) > 3200

    excerpt = make_excerpt(END_OF_CHUNK, question=QUESTION, answer=ANSWER_ABOUT_DECLINE)

    assert excerpt == END_SENTENCE
    assert END_OF_CHUNK.endswith(excerpt)


def test_equal_scores_go_to_the_earlier_sentence() -> None:
    content = "Cash rose sharply! Cash fell later? Nothing else."

    assert make_excerpt(content, question="cash", answer="") == "Cash rose sharply!"


def test_newlines_are_sentence_boundaries_and_spans_are_trimmed() -> None:
    content = "  Heading without a period\n\t Revenue fell in Europe  \nFooter"

    excerpt = make_excerpt(content, question="revenue europe", answer="")

    assert excerpt == "Revenue fell in Europe"


def test_a_period_without_following_whitespace_is_not_a_boundary() -> None:
    content = "Version 2.5 raised revenue. Costs were flat."

    assert make_excerpt(content, question="version", answer="") == (
        "Version 2.5 raised revenue."
    )


def test_a_long_sentence_gives_the_earliest_densest_word_window() -> None:
    phrase = "european revenue declined because of currency headwinds"
    content = " ".join(["lorem"] * 90 + [phrase] + ["lorem"] * 90) + "."
    assert len(content) > 2 * EXCERPT_MAX_CHARS

    excerpt = make_excerpt(content, question=QUESTION, answer=ANSWER_ABOUT_DECLINE)

    assert_bounded_substring(excerpt, content)
    assert phrase in excerpt
    start = content.index(excerpt)
    end = start + len(excerpt)
    assert start == 0 or content[start - 1] == " "
    assert end == len(content) or content[end] == " "
    phrase_end = content.index(phrase) + len(phrase)
    # One word earlier the window could no longer reach the end of the phrase.
    assert phrase_end - (start - len("lorem ")) > EXCERPT_MAX_CHARS


def test_a_word_longer_than_the_limit_is_cut_to_the_limit() -> None:
    content = "x" * 500 + " tail."

    assert make_excerpt(content, question="nothing", answer="") == "x" * 400


def test_no_overlap_gives_a_word_boundary_prefix() -> None:
    excerpt = make_excerpt(
        END_OF_CHUNK, question="What is Initech's dividend policy?", answer=""
    )

    assert_bounded_substring(excerpt, END_OF_CHUNK)
    assert END_OF_CHUNK.startswith(excerpt)
    assert END_OF_CHUNK[len(excerpt)] == " "
    assert len(excerpt) > EXCERPT_MAX_CHARS - len("governance ")


def test_the_fallback_starts_at_the_first_non_whitespace_character() -> None:
    content = " \n\t Headcount was flat. Margins improved.\n"

    assert make_excerpt(content, question="dividend", answer="") == (
        "Headcount was flat. Margins improved."
    )


def test_short_tokens_are_ignored() -> None:
    content = "An ox is at it. Dividends rose."

    assert make_excerpt(content, question="an ox is at it", answer="dividends") == (
        "Dividends rose."
    )


def test_matching_is_case_insensitive() -> None:
    content = "Costs were flat. EUROPEAN REVENUE FELL."

    assert make_excerpt(content, question="european revenue", answer="") == (
        "EUROPEAN REVENUE FELL."
    )


def test_citation_markers_in_the_answer_do_not_score() -> None:
    """Without removal, ``[D1234]`` would tokenize to ``d1234`` and match."""
    content = "Alpha d1234 here. Beta sentence."

    assert make_excerpt(content, question="zzz", answer="[D1234]") == content


def test_paraphrase_is_deterministic() -> None:
    paraphrase = "Sales in Europe fell because of exchange rates"

    first = make_excerpt(END_OF_CHUNK, question=paraphrase, answer=paraphrase)
    second = make_excerpt(END_OF_CHUNK, question=paraphrase, answer=paraphrase)

    assert first == second == END_SENTENCE


@pytest.mark.parametrize("content", ["", "\n\t\n", "     "])
def test_blank_content_gives_an_empty_excerpt(content: str) -> None:
    assert make_excerpt(content, question=QUESTION, answer=ANSWER_ABOUT_DECLINE) == ""


@pytest.mark.parametrize(
    ("content", "question"),
    [
        (END_OF_CHUNK, QUESTION),
        (END_OF_CHUNK, "dividend"),
        ("x" * 1000, "x"),
        ("word " * 300, "word"),
        ("Short.\n\n  Two  spaced   words.  ", "spaced words"),
        ("é" * 20 + " Revenue fell. " + "ß" * 450, "revenue"),
    ],
)
def test_every_excerpt_is_a_bounded_exact_substring(
    content: str, question: str
) -> None:
    excerpt = make_excerpt(content, question=question, answer="")

    assert_bounded_substring(excerpt, content)
