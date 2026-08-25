"""Unit tests for format_profile_fields.

This is the only thing that turns the profile form into text the compiler
sees, so anything it silently drops never reaches the notes — and the failure
is invisible, because the notes still generate, just without that answer.
"""

from app.services.notes_graph import format_profile_fields


def test_empty_profile_renders_nothing() -> None:
    # _user_profile() branches on this being falsy to decide "never filled in"
    # versus "compilation failed", so an empty dict must produce an empty
    # string, not a set of bare labels.
    assert format_profile_fields({}, "") == ""


def test_blank_answers_are_omitted_not_labelled() -> None:
    assert format_profile_fields({"occupation": "   ", "extra": ""}, "  ") == ""


def test_name_is_included() -> None:
    assert "Name: Karl" in format_profile_fields({}, "Karl")


def test_every_answered_field_survives() -> None:
    rendered = format_profile_fields(
        {
            "occupation": "Software Engineer",
            "education_level": "Undergraduate",
            "background_level": "Some background",
            "notes_purpose": ["Revision", "Exams"],
            "emphasize": ["Definitions"],
            "extra": "Prefers worked examples",
        },
        "Karl",
    )
    for expected in (
        "Karl",
        "Software Engineer",
        "Undergraduate",
        "Some background",
        "Revision",
        "Exams",
        "Definitions",
        "Prefers worked examples",
    ):
        assert expected in rendered, f"{expected!r} was dropped"


def test_multi_select_lists_are_joined() -> None:
    rendered = format_profile_fields({"notes_purpose": ["Revision", "Exams"]}, "")
    assert "Revision, Exams" in rendered


def test_tolerates_a_string_where_a_list_is_expected() -> None:
    # notes_purpose was a single string before it became multi-select, so old
    # rows still hold one. Raising here would break note generation entirely.
    assert "Revision" in format_profile_fields({"notes_purpose": "Revision"}, "")


def test_ignores_unknown_keys() -> None:
    # The form keeps changing shape; a removed field left in an old jsonb row
    # must not appear in the compiled profile.
    rendered = format_profile_fields({"occupation": "Engineer", "legacy_field": "junk"}, "")
    assert "junk" not in rendered


def test_one_line_per_answer() -> None:
    rendered = format_profile_fields({"occupation": "Engineer", "extra": "Likes examples"}, "Karl")
    assert len(rendered.splitlines()) == 3
