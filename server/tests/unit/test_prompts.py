"""Unit tests for prompt assembly and the routing rules.

Prompts are half of a contract with the Pydantic model each node returns, and
the containment properties below are the ones that keep user-supplied text
away from the decisions the code depends on.
"""

from app.services.notes_graph import (
    DEFAULT_CHAT_INSTRUCTIONS,
    DEFAULT_NEW_NOTES_INSTRUCTIONS,
    DEFAULT_NOTES_INSTRUCTIONS,
    DEFAULT_ROUTING_INSTRUCTIONS,
    _build_chat_prompt,
    _build_notes_prompt,
    _build_routing_prompt,
    _history_messages,
    _note_context,
)

PROFILE = "Karl is a software engineer studying linear algebra."


class TestPromptComposition:
    def test_starting_a_document_uses_the_new_notes_instructions(self) -> None:
        assert DEFAULT_NEW_NOTES_INSTRUCTIONS in _build_notes_prompt("", starting_new=True)

    def test_extending_a_document_uses_the_extend_instructions(self) -> None:
        assert DEFAULT_NOTES_INSTRUCTIONS in _build_notes_prompt("", starting_new=False)

    def test_the_two_note_branches_differ(self) -> None:
        assert _build_notes_prompt("", starting_new=True) != _build_notes_prompt("", starting_new=False)

    def test_profile_is_included_when_present(self) -> None:
        assert PROFILE in _build_notes_prompt(PROFILE, starting_new=False)
        assert PROFILE in _build_chat_prompt(PROFILE)

    def test_profile_block_is_omitted_entirely_when_empty(self) -> None:
        # Not "included but blank" — an empty heading invites the model to
        # invent something to put under it. The guard sentence only exists to
        # introduce the profile, so its absence proves the block is gone.
        with_profile = _build_notes_prompt(PROFILE, starting_new=False)
        without = _build_notes_prompt("", starting_new=False)
        assert "Never mention" in with_profile
        assert "Never mention" not in without
        assert len(without) < len(with_profile)

    def test_profile_always_carries_its_guard(self) -> None:
        # Without this the model treats the profile as material to write
        # about and adds a section about the reader to the notes.
        prompt = _build_notes_prompt(PROFILE, starting_new=False)
        assert "Never mention" in prompt


class TestRouterContainment:
    """The router must never see personalization: whether an input deserves a
    note change has nothing to do with who the user is."""

    def test_router_prompt_has_no_profile_hook(self) -> None:
        # _build_routing_prompt takes no profile argument at all — this asserts
        # the signature stays that way.
        assert PROFILE not in _build_routing_prompt()

    def test_router_prompt_is_base_plus_routing_only(self) -> None:
        prompt = _build_routing_prompt()
        assert DEFAULT_ROUTING_INSTRUCTIONS in prompt
        assert DEFAULT_CHAT_INSTRUCTIONS not in prompt
        assert DEFAULT_NOTES_INSTRUCTIONS not in prompt


class TestRoutingRules:
    """Regression cover for the "yes" loop: four turns of confirmation that
    each routed to chat because a bare "yes" matched the filler examples."""

    def test_affirmatives_are_routed_by_what_they_agree_to(self) -> None:
        rules = DEFAULT_ROUTING_INSTRUCTIONS
        assert "ALREADY ON THE TABLE" in rules
        for affirmative in ("yes", "all", "sure", "do it"):
            assert f"`{affirmative}`" in rules, f"{affirmative!r} is not listed as an affirmative"

    def test_filler_rule_carves_out_answers(self) -> None:
        # The filler rule and the affirmation rule would otherwise contradict
        # each other, and this model resolves a contradiction by doing nothing.
        assert "does not apply to an answer" in DEFAULT_ROUTING_INSTRUCTIONS

    def test_polite_requests_are_instructions(self) -> None:
        assert "courtesy" in DEFAULT_ROUTING_INSTRUCTIONS

    def test_genuine_questions_still_route_to_chat(self) -> None:
        assert "is X worth adding?" in DEFAULT_ROUTING_INSTRUCTIONS

    def test_chat_branch_must_not_re_ask(self) -> None:
        assert "never re-ask" in DEFAULT_CHAT_INSTRUCTIONS


class TestNoteContext:
    def test_includes_the_document(self) -> None:
        state = {"current_note": "# Vectors\n\nA vector has magnitude.", "current_title": "Vectors"}
        assert "A vector has magnitude." in _note_context(state)  # type: ignore[arg-type]

    def test_includes_a_real_title(self) -> None:
        state = {"current_note": "notes", "current_title": "Linear Algebra"}
        assert "Linear Algebra" in _note_context(state)  # type: ignore[arg-type]

    def test_placeholder_title_is_not_offered_for_preservation(self) -> None:
        # The prompts ask the model to keep an existing title unless the
        # subject shifted; handing it "New conversation" makes it keep that.
        state = {"current_note": "notes", "current_title": "New conversation"}
        assert "New conversation" not in _note_context(state)  # type: ignore[arg-type]

    def test_empty_notes_say_so_explicitly(self) -> None:
        state = {"current_note": "", "current_title": ""}
        assert "No notes document exists yet" in _note_context(state)  # type: ignore[arg-type]


class TestHistoryMessages:
    def test_roles_map_to_the_right_message_types(self) -> None:
        messages = _history_messages(
            [
                {"role": "user", "content": "what is a vector?"},
                {"role": "assistant", "content": "A quantity with magnitude and direction."},
            ]
        )
        assert [type(m).__name__ for m in messages] == ["HumanMessage", "AIMessage"]

    def test_order_is_preserved(self) -> None:
        # The affirmation rule depends on the previous assistant turn being
        # readable in sequence.
        messages = _history_messages([{"role": "user", "content": f"turn {i}"} for i in range(5)])
        assert [m.content for m in messages] == [f"turn {i}" for i in range(5)]

    def test_empty_history_is_empty(self) -> None:
        assert _history_messages([]) == []
