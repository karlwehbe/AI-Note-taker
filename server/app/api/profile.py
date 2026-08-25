import logging
from datetime import UTC, datetime


from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import UserProfile
from app.services.notes_graph import (
    MAX_INSTRUCTIONS_LEN,
    compile_profile,
    format_profile_fields,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

MAX_NAME_LEN = 200
MAX_SHORT_LEN = 200


class ProfileFields(BaseModel):
    occupation: str = Field("", max_length=MAX_SHORT_LEN)
    # beginner | some familiarity | experienced
    background_level: str = Field("", max_length=MAX_SHORT_LEN)
    education_level: str = Field("", max_length=MAX_SHORT_LEN)
    notes_purpose: list[str] = Field(default_factory=list)
    emphasize: list[str] = Field(default_factory=list)
    # The user's own directions for the AI, passed to the writer verbatim.
    # Named `extra` before it became Instructions; the validator below keeps
    # rows saved under the old key working.
    instructions: str = Field("", max_length=MAX_INSTRUCTIONS_LEN)

    @field_validator("notes_purpose", mode="before")
    @classmethod
    def _coerce_legacy_single_value(cls, value: object) -> object:
        """notes_purpose used to be a single string. Rows saved before it
        became multi-select would fail validation otherwise."""
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_extra_key(cls, data: object) -> object:
        """`extra` was this field's name before it became Instructions.

        A field_validator can't do this: the old rows have no `instructions`
        key at all, so nothing would fire. Runs on the whole payload instead,
        and only fills in when the new key is genuinely absent — so a real
        empty string the user saved is never overwritten by stale text.
        """
        if isinstance(data, dict) and "instructions" not in data and "extra" in data:
            data = {**data, "instructions": data["extra"]}
        return data


class ProfileIn(BaseModel):
    name: str = Field("", max_length=MAX_NAME_LEN)
    fields: ProfileFields = Field(default_factory=ProfileFields)


class ProfileOut(BaseModel):
    """Deliberately does NOT carry compiled_prompt.

    The compiled description is generated and used, but never shown: it is a
    paraphrase of the user rather than anything they control, and exposing it
    is what required the whole hand-edit/regenerate/is_edited mechanism.
    compile_failed_at is omitted for the same reason — with nothing visible
    to retry, _user_profile()'s lazy recompile is the recovery path.
    """

    name: str
    fields: ProfileFields
    has_profile: bool


def _to_out(profile: UserProfile | None) -> ProfileOut:
    if profile is None:
        return ProfileOut(name="", fields=ProfileFields(), has_profile=False)
    fields = ProfileFields(**(profile.fields or {}))
    answered = any(bool(v) for v in fields.model_dump().values())
    return ProfileOut(
        name=profile.name,
        fields=fields,
        has_profile=bool(profile.name.strip()) or answered,
    )


async def _try_compile(profile: UserProfile, settings: Settings, db: Session) -> str | None:
    """Compiles, or returns None if it failed. Never raises: the caller has
    already committed the user's answers and must not fail because a derived
    field couldn't be produced."""
    try:
        return await compile_profile(profile.fields or {}, profile.name, settings)
    except Exception:
        logger.exception("Profile compilation failed")
        return None


def _compile_onto(profile: UserProfile, compiled: str | None) -> None:
    """Applies a compilation result. A failure leaves compiled_prompt NULL and
    stamps compile_failed_at — no longer surfaced to the user, but it is what
    distinguishes "never filled in" from "tried and broke" in the logs and for
    _user_profile()'s lazy retry."""
    if compiled:
        profile.compiled_prompt = compiled
        profile.compile_failed_at = None
        return
    profile.compiled_prompt = None
    # An empty profile isn't a failure — there was simply nothing to compile.
    has_answers = bool(format_profile_fields(profile.fields or {}, profile.name).strip())
    profile.compile_failed_at = datetime.now(UTC) if has_answers else None


@router.get("", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)) -> ProfileOut:
    return _to_out(db.scalar(select(UserProfile)))


@router.put("", response_model=ProfileOut)
async def save_profile(
    payload: ProfileIn,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ProfileOut:
    profile = db.scalar(select(UserProfile))
    if profile is None:
        profile = UserProfile()
        db.add(profile)

    previous_fields = dict(profile.fields or {})
    previous_name = profile.name
    profile.name = payload.name.strip()
    profile.fields = payload.fields.model_dump()

    # Recompile whenever an answer actually changed. There is no hand-edited
    # version to protect any more, so no confirmation step. `name` is compiled
    # too and lives outside `fields`, so it has to be part of this comparison
    # or a name-only edit would never recompile.
    fields_changed = profile.fields != previous_fields or profile.name != previous_name
    should_compile = fields_changed

    # Committed BEFORE the LLM is touched. The answers are the user's data and
    # are durable from here on; compilation is derived text that can fail,
    # be retried, or be edited later without ever risking what they typed.
    db.commit()
    db.refresh(profile)
    logger.info("Profile saved (fields_changed=%s, compiling=%s)", fields_changed, should_compile)

    if should_compile:
        _compile_onto(profile, await _try_compile(profile, settings, db))
        db.commit()
        db.refresh(profile)
    # The save itself succeeded either way — a compilation failure only means
    # notes generate without the personal layer, never an error status here.
    return _to_out(profile)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(db: Session = Depends(get_db)) -> None:
    profile = db.scalar(select(UserProfile))
    if profile is not None:
        db.delete(profile)
        db.commit()
    logger.info("Profile cleared")
