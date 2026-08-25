import logging
from datetime import UTC, datetime


from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import UserProfile
from app.services.notes_graph import (
    MAX_COMPILED_PROMPT_LEN,
    compile_profile,
    format_profile_fields,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

MAX_NAME_LEN = 200
MAX_SHORT_LEN = 200
# Short on purpose: free text here competes with lecture content for the
# model's attention on every single generation call.
MAX_EXTRA_LEN = 200


class ProfileFields(BaseModel):
    occupation: str = Field("", max_length=MAX_SHORT_LEN)
    # beginner | some familiarity | experienced
    background_level: str = Field("", max_length=MAX_SHORT_LEN)
    education_level: str = Field("", max_length=MAX_SHORT_LEN)
    notes_purpose: list[str] = Field(default_factory=list)
    emphasize: list[str] = Field(default_factory=list)
    extra: str = Field("", max_length=MAX_EXTRA_LEN)

    @field_validator("notes_purpose", mode="before")
    @classmethod
    def _coerce_legacy_single_value(cls, value: object) -> object:
        """notes_purpose used to be a single string. Rows saved before it
        became multi-select would fail validation otherwise."""
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


class ProfileIn(BaseModel):
    name: str = Field("", max_length=MAX_NAME_LEN)
    fields: ProfileFields = Field(default_factory=ProfileFields)
    # Only consulted when the compiled text was hand-edited: the client asks
    # first, and passes true if the user agreed to discard their version.
    regenerate: bool = False


class ProfileOut(BaseModel):
    name: str
    fields: ProfileFields
    compiled_prompt: str | None
    is_edited: bool
    has_profile: bool
    # Non-null with a null compiled_prompt means compilation was attempted
    # and failed — the client shows an inline retry rather than treating the
    # save itself as an error.
    compile_failed_at: datetime | None


class CompiledPromptIn(BaseModel):
    compiled_prompt: str = Field("", max_length=MAX_COMPILED_PROMPT_LEN)


def _to_out(profile: UserProfile | None) -> ProfileOut:
    if profile is None:
        return ProfileOut(
            name="",
            fields=ProfileFields(),
            compiled_prompt=None,
            is_edited=False,
            has_profile=False,
            compile_failed_at=None,
        )
    fields = ProfileFields(**(profile.fields or {}))
    answered = any(bool(v) for v in fields.model_dump().values())
    return ProfileOut(
        name=profile.name,
        fields=fields,
        compiled_prompt=profile.compiled_prompt,
        is_edited=profile.is_edited,
        has_profile=bool(profile.name.strip()) or answered,
        compile_failed_at=profile.compile_failed_at,
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
    """Applies a compilation result. A failure leaves compiled_prompt NULL
    and stamps compile_failed_at, which is what lets the UI tell "never
    filled in" apart from "tried and broke"."""
    if compiled:
        profile.compiled_prompt = compiled
        profile.is_edited = False
        profile.compile_failed_at = None
        return
    profile.compiled_prompt = None
    profile.is_edited = False
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

    # Recompile when any answer actually changed — but never silently over a
    # hand-edited prompt: that needs explicit confirmation from the client.
    # `name` is compiled too, and lives outside `fields`, so it has to be
    # part of this comparison or a name-only edit would never recompile.
    fields_changed = profile.fields != previous_fields or profile.name != previous_name
    should_compile = fields_changed and (not profile.is_edited or payload.regenerate)

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
    # The save itself succeeded either way — a compilation failure is
    # reported through compile_failed_at, not an error status.
    return _to_out(profile)


@router.post("/regenerate", response_model=ProfileOut)
async def regenerate_prompt(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ProfileOut:
    """Explicit "Regenerate" from the UI. Awaited rather than backgrounded —
    the user asked for it and is watching for the result."""
    profile = db.scalar(select(UserProfile))
    if profile is None:
        return _to_out(None)
    _compile_onto(profile, await _try_compile(profile, settings, db))
    db.commit()
    db.refresh(profile)
    return _to_out(profile)


@router.put("/prompt", response_model=ProfileOut)
def save_compiled_prompt(payload: CompiledPromptIn, db: Session = Depends(get_db)) -> ProfileOut:
    """The user rewriting the compiled text by hand. Flags is_edited so a
    later field change can't quietly discard it."""
    profile = db.scalar(select(UserProfile))
    if profile is None:
        profile = UserProfile()
        db.add(profile)
    text = payload.compiled_prompt.strip()
    profile.compiled_prompt = text or None
    profile.is_edited = bool(text)
    if text:
        profile.compile_failed_at = None
    db.commit()
    db.refresh(profile)
    logger.info("Compiled prompt hand-edited (%d chars)", len(text))
    return _to_out(profile)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(db: Session = Depends(get_db)) -> None:
    profile = db.scalar(select(UserProfile))
    if profile is not None:
        db.delete(profile)
        db.commit()
    logger.info("Profile cleared")
