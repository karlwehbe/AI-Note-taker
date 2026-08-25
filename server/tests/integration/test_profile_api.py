"""Integration tests: the profile API.

Two things go to the model from here. The answers are compiled into a private
description of the user — never returned by the API, so these assert against
the DB row. Instructions are the user's own text, stored verbatim and kept out
of the compiler entirely.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import UserProfile

COMPILED = "Write for a software engineer with some linear algebra background."

ANSWERS = {
    "name": "Karl",
    "fields": {
        "occupation": "Software Engineer",
        "education_level": "Undergraduate",
        "background_level": "Some background",
        "notes_purpose": ["Revision"],
        "emphasize": ["Definitions"],
        "instructions": "",
    },
}


@pytest.fixture
def stub_compile(monkeypatch: pytest.MonkeyPatch):
    """Stubs the LLM compile step. Patched at its use site in app.api.profile."""

    def _stub(result: str | None = COMPILED, *, raises: Exception | None = None) -> list[dict]:
        calls: list[dict] = []

        async def fake_compile_profile(fields, name, settings):
            calls.append({"fields": fields, "name": name})
            if raises is not None:
                raise raises
            return result

        monkeypatch.setattr("app.api.profile.compile_profile", fake_compile_profile)
        return calls

    return _stub


class TestEmptyProfile:
    def test_get_with_no_row(self, client: TestClient) -> None:
        response = client.get("/profile")
        assert response.status_code == 200
        assert response.json()["has_profile"] is False

    def test_delete_with_no_row_is_still_204(self, client: TestClient) -> None:
        assert client.delete("/profile").status_code == 204


class TestSaveAndCompile:
    def test_answers_are_saved_and_compiled(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        response = client.put("/profile", json=ANSWERS)
        assert response.status_code == 200

        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt == COMPILED
        assert profile.name == "Karl"
        assert profile.fields["occupation"] == "Software Engineer"

    def test_multi_select_survives_the_jsonb_round_trip(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        client.put("/profile", json=ANSWERS)
        assert db.query(UserProfile).one().fields["notes_purpose"] == ["Revision"]

    def test_resaving_identical_answers_skips_the_compile(self, client: TestClient, stub_compile) -> None:
        # Compilation is the slow, paid part; an untouched form must cost nothing.
        calls = stub_compile()
        client.put("/profile", json=ANSWERS)
        assert len(calls) == 1
        client.put("/profile", json=ANSWERS)
        assert len(calls) == 1, "an unchanged form triggered a second compile"

    def test_a_name_only_change_still_recompiles(self, client: TestClient, stub_compile) -> None:
        # name lives outside `fields`, so change detection has to compare both.
        calls = stub_compile()
        client.put("/profile", json=ANSWERS)
        client.put("/profile", json={**ANSWERS, "name": "Karl W"})
        assert len(calls) == 2

    def test_answers_survive_a_compile_failure(self, client: TestClient, db: Session, stub_compile) -> None:
        # The answers are committed before the LLM is touched, so what the
        # user typed is durable even when the derived text fails.
        stub_compile(raises=RuntimeError("provider down"))
        response = client.put("/profile", json=ANSWERS)

        assert response.status_code == 200, "a compile failure must not fail the save"
        profile = db.query(UserProfile).one()
        assert profile.fields["occupation"] == "Software Engineer"
        assert profile.compiled_prompt is None
        assert profile.compile_failed_at is not None

    def test_over_long_answers_are_rejected(self, client: TestClient) -> None:
        payload = {**ANSWERS, "fields": {**ANSWERS["fields"], "occupation": "x" * 500}}
        assert client.put("/profile", json=payload).status_code == 422

    def test_over_long_instructions_are_rejected(self, client: TestClient) -> None:
        # The cap is the only bound on text that reaches the prompt verbatim.
        payload = {**ANSWERS, "fields": {**ANSWERS["fields"], "instructions": "x" * 700}}
        assert client.put("/profile", json=payload).status_code == 422


class TestInstructions:
    """The user's own directions: stored as typed, never compiled, never
    exposed alongside a description they cannot see."""

    INSTRUCTIONS = "End every section with a one-line summary.\nUse tables for comparisons."

    def test_stored_exactly_as_typed(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        payload = {**ANSWERS, "fields": {**ANSWERS["fields"], "instructions": self.INSTRUCTIONS}}
        client.put("/profile", json=payload)
        assert db.query(UserProfile).one().fields["instructions"] == self.INSTRUCTIONS

    def test_round_trips_through_the_api(self, client: TestClient, stub_compile) -> None:
        stub_compile()
        payload = {**ANSWERS, "fields": {**ANSWERS["fields"], "instructions": self.INSTRUCTIONS}}
        client.put("/profile", json=payload)
        assert client.get("/profile").json()["fields"]["instructions"] == self.INSTRUCTIONS

    def test_legacy_extra_key_is_read_as_instructions(self, client: TestClient, db: Session, stub_compile) -> None:
        # Rows written before the rename hold `extra` and no `instructions`
        # key at all. The response must surface that text under the new name
        # rather than showing the user an empty box.
        stub_compile()
        client.put("/profile", json=ANSWERS)
        profile = db.query(UserProfile).one()
        legacy = {k: v for k, v in profile.fields.items() if k != "instructions"}
        profile.fields = {**legacy, "extra": "Old free text"}  # reassign: JSONB isn't tracked
        db.commit()

        assert client.get("/profile").json()["fields"]["instructions"] == "Old free text"


class TestPrivateDescription:
    """The compiled description is generated and used, but never returned."""

    def test_not_in_the_save_response(self, client: TestClient, stub_compile) -> None:
        stub_compile()
        body = client.put("/profile", json=ANSWERS).json()
        assert "compiled_prompt" not in body
        assert COMPILED not in str(body)

    def test_not_in_the_get_response(self, client: TestClient, stub_compile) -> None:
        stub_compile()
        client.put("/profile", json=ANSWERS)
        assert COMPILED not in str(client.get("/profile").json())

    def test_the_hand_edit_endpoints_are_gone(self, client: TestClient) -> None:
        # They existed only to expose and protect the visible description.
        # 404, not 405: the paths themselves no longer exist on the router.
        assert client.put("/profile/prompt", json={"compiled_prompt": "mine"}).status_code == 404
        assert client.post("/profile/regenerate").status_code == 404


class TestThreeStates:
    """compiled_prompt alone is ambiguous — null could mean "never filled in"
    or "we tried and it broke". compile_failed_at is what separates them."""

    def test_never_filled_in(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile(None)
        client.put("/profile", json={"name": "", "fields": {}})
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt is None
        assert profile.compile_failed_at is None, "an empty profile is not a failure"

    def test_tried_and_failed(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile(raises=RuntimeError("provider down"))
        client.put("/profile", json=ANSWERS)
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt is None
        assert profile.compile_failed_at is not None

    def test_success_clears_a_previous_failure(self, client: TestClient, db: Session, stub_compile) -> None:
        # No Regenerate endpoint any more — saving a changed answer is the
        # only way to recompile, and it no longer asks permission.
        stub_compile(raises=RuntimeError("provider down"))
        client.put("/profile", json=ANSWERS)
        stub_compile()
        client.put("/profile", json={**ANSWERS, "name": "Karl W"})
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt == COMPILED
        assert profile.compile_failed_at is None


class TestDelete:
    def test_delete_removes_the_row(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        client.put("/profile", json=ANSWERS)
        assert client.delete("/profile").status_code == 204
        assert db.query(UserProfile).count() == 0
