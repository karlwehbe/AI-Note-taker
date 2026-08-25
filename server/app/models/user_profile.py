import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserProfile(Base):
    """The personal context layer: a short profile, compiled by an LLM into
    two or three sentences that ride along on every note-generation prompt.

    Single-row table — no auth in this app, so saving replaces the row.
    """

    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # UI only. Deliberately never sent to compilation: it can't inform depth
    # or vocabulary, and naming the reader invites the model to address them
    # ("given that Karl is a second-year student…") instead of just writing
    # good notes.
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Raw form answers. JSONB rather than columns because two of the answers
    # are multi-select, and because the form will keep changing shape.
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # NULL means no personal layer — notes generate normally without one.
    # Never empty string: null is the single "absent" state.
    compiled_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the user rewrites the compiled text by hand. Regeneration then
    # requires explicit confirmation, so changing a form field can't silently
    # discard what they wrote.
    is_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when a compilation attempt fails. Without it, a null
    # compiled_prompt is ambiguous: it could mean the user never filled the
    # profile in, or that we tried and it broke. Cleared on every success.
    compile_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
