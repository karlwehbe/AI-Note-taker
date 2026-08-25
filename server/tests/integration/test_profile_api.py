"""Integration tests: the profile API.

The interesting part is the three-state machine around compiled_prompt and
compile_failed_at, and the is_edited guard that stops a form change silently
overwriting text the user rewrote by hand.
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
        "extra": "",
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
        assert response.json()["compiled_prompt"] is None

    def test_delete_with_no_row_is_still_204(self, client: TestClient) -> None:
        assert client.delete("/profile").status_code == 204


class TestSaveAndCompile:
    def test_answers_are_saved_and_compiled(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        response = client.put("/profile", json=ANSWERS)
        assert response.status_code == 200
        assert response.json()["compiled_prompt"] == COMPILED

        profile = db.query(UserProfile).one()
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
        stub_compile(raises=RuntimeError("provider down"))
        client.put("/profile", json=ANSWERS)
        stub_compile()
        client.post("/profile/regenerate")
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt == COMPILED
        assert profile.compile_failed_at is None


class TestHandEditedPrompt:
    def test_editing_sets_is_edited(self, client: TestClient, db: Session) -> None:
        client.put("/profile/prompt", json={"compiled_prompt": "My own wording."})
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt == "My own wording."
        assert profile.is_edited is True

    def test_a_form_change_does_not_silently_overwrite_an_edit(
        self, client: TestClient, db: Session, stub_compile
    ) -> None:
        calls = stub_compile()
        client.put("/profile/prompt", json={"compiled_prompt": "My own wording."})
        client.put("/profile", json=ANSWERS)  # regenerate not requested

        assert calls == [], "a hand-edited prompt was recompiled without confirmation"
        assert db.query(UserProfile).one().compiled_prompt == "My own wording."

    def test_explicit_regenerate_overrides_the_edit(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        client.put("/profile/prompt", json={"compiled_prompt": "My own wording."})
        client.put("/profile", json={**ANSWERS, "regenerate": True})
        assert db.query(UserProfile).one().compiled_prompt == COMPILED

    def test_clearing_the_prompt_unsets_is_edited(self, client: TestClient, db: Session) -> None:
        client.put("/profile/prompt", json={"compiled_prompt": "My own wording."})
        client.put("/profile/prompt", json={"compiled_prompt": "   "})
        profile = db.query(UserProfile).one()
        assert profile.compiled_prompt is None
        assert profile.is_edited is False


class TestDelete:
    def test_delete_removes_the_row(self, client: TestClient, db: Session, stub_compile) -> None:
        stub_compile()
        client.put("/profile", json=ANSWERS)
        assert client.delete("/profile").status_code == 204
        assert db.query(UserProfile).count() == 0
