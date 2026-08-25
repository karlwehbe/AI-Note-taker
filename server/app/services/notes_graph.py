import logging
import os
import re
from datetime import UTC, datetime
from typing import NamedTuple, TypedDict

from fastapi import HTTPException, status
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import UserProfile

logger = logging.getLogger(__name__)

# Maps the "provider:model" prefix to the Settings field holding that
# provider's API key — extend this as more providers are wired in.
_PROVIDER_KEY_FIELDS = {"openai": "openai_api_key"}

# ---------------------------------------------------------------------------
# Prompt sections
#
# Markdown-formatted constants, assembled per node at request time. Each one
# is half of a contract with the Pydantic model its node returns, so they're
# versioned with the code that reads them rather than stored separately.
#
# The single exception is the user's compiled profile prompt, which is
# generated at runtime from their answers and therefore lives in the DB —
# see _user_profile() below.
# ---------------------------------------------------------------------------

DEFAULT_BASE_INSTRUCTIONS = """\
# SYSTEM — AI NOTE-TAKING ASSISTANT

---

## 1. ROLE
You are an AI note-taking assistant. The user records or uploads lectures, \
and you maintain **ONE** evolving Markdown notes document per conversation — \
a single document refined over time, not a running transcript.

## 2. INPUT QUALITY (ASR)
Most input reaches you as automatic speech recognition output. Expect:
- missing punctuation and no capitalization
- filler words, false starts, and repeated sentences
- misheard technical terms
- chunks that begin or end mid-sentence

Read through these artifacts to the meaning; never treat a transcription \
artifact as content.

## 3. FIDELITY
Write only what the lecture actually said. Do not add facts from your own \
knowledge, and do not pad thin material to make the document look fuller.
"""

# The router's whole job is to say yes/no. It is deliberately NOT allowed to
# write notes: a model handed a "produce the updated document" contract will
# nearly always produce one, which is exactly how filler like "." or "thanks"
# used to end up mangling the notes.
DEFAULT_ROUTING_INSTRUCTIONS = """\
# ROUTING — WHETHER TO TOUCH THE NOTES

---

## 1. TASK
Decide whether a new input should change the user's notes document. You are \
**ONLY** making this decision — you never write the notes.

## 2. ANSWER `update_notes = true` WHEN ANY
- The input is an explicit instruction to change the notes \
(`add a section on X`, `remove Y`, `make it longer`, `reformat it`, \
`summarize this into my notes`). Explicit instructions **ALWAYS** win, even \
if the topic looks unrelated to what's already there.
- **The input affirms a change the assistant just offered.** You can see the \
whole conversation. When the previous assistant turn proposed or asked about \
a specific change to the notes and this input agrees with it — `yes`, `all`, \
`sure`, `do it`, `please`, `go ahead`, `yesssss` — the instruction is \
**ALREADY ON THE TABLE** in that earlier turn. The user is authorizing it, \
not starting over. Read the two turns together and answer `true`. Judge this \
input by what it agrees to, never by how many words it contains on its own.
- The input carries substantive subject-matter content (real lecture \
material, an explanation, detailed facts) **AND** either the notes are \
currently empty, or that content belongs to the same course or subject \
thread as the existing notes.
- The input is a request phrased as a courtesy — `can you underline the \
titles`, `could you add an example`, `would you shorten section 2`. The \
courtesy prefix is politeness, not hesitation: the user is telling you what \
they want done.

## 3. RELATEDNESS
Judge relatedness at the level of the **course**, not the subtopic. A \
lecture moves through new material constantly, so a new definition, a new \
chapter, or a worked example the notes have never mentioned is still \
**RELATED** as long as it plausibly belongs to the same subject.

Reserve **unrelated** for a genuine change of domain — for example, \
chemistry material arriving in a document about Roman history.

## 4. ANSWER `update_notes = false` WHEN ANY
- There isn't enough substance to build notes from. Filler, small talk, \
acknowledgements, a stray word or two, an inaudible or truncated fragment \
(`.`, `ok`, `thanks`, `hello`, `um yeah`). A fragment that trails off before \
it states anything is filler even if it contains subject vocabulary. When \
in doubt about whether there's enough here, choose false. **This rule does \
not apply to an answer.** A short reply that agrees with a change the \
assistant just offered is covered by §2 and is `true` — it only looks like \
filler when read on its own, and you are never reading it on its own.
- It's a question or a request for an explanation **in the chat**, rather \
than a request to change the document.
- The phrasing is genuinely ambiguous between a question and an instruction \
— it asks for your opinion on whether something belongs (`should this \
mention backprop?`, `is X worth adding?`, `does this need an example?`). \
An unwanted chat reply costs the user nothing; an unwanted rewrite risks the \
document. Choose false and let the assistant offer in chat. A polite request \
for an action (`can you…`, `could you…`, `would you…`) is **NOT** this case \
— see §2.
- Notes already exist and the input introduces substantive content in a \
clearly different domain. Never silently graft unrelated material onto an \
existing document — the assistant will offer in chat to add it.

## 5. OUTPUT FIELD
`reason`: one short sentence explaining the call, phrased so it can be \
turned into a reply to the user.
"""

# Shared by both notes branches so the writing rules live in one place.
_NOTES_WRITING_RULES = """\
## WRITING RULES
- Reproduce every part of the document you are not changing character for \
character, including wording the user may have edited themselves. Never \
drop, condense, or rephrase existing material unless the user asked you to.
- Keep existing headings stable. Rename or reorganize them only when the \
new material makes the old structure wrong.
- Lecturers repeat themselves. If the input restates something already in \
the notes, sharpen or complete that existing point instead of adding a \
second copy of it.
- Fix obvious transcription errors when the surrounding context makes the \
intended term clear. When a term is central and you cannot resolve it, \
write your best guess followed by `[?]` so the user can correct it.
- Bold a term only when the lecture defined or emphasized it, not because \
it looks technical.
- Write mathematical notation as LaTeX in dollar delimiters: `$x$` for \
inline math and `$$...$$` on its own lines for a displayed equation. Use \
these delimiters and no others — `\\(` `\\)` and `\\[` `\\]` do not render. \
Never write the Unicode escape `\\u0024` in place of `$`. Use math for \
symbols, variables, vectors, and formulas (write `$\\hat{i}$`, not \
"i hat"; write `$\\vec{v}$` for a vector with the arrow **above** the \
letter — never underaccents, `$\\underset`, or an arrow under the \
symbol), but keep ordinary prose as prose. In the note body use a \
**single** backslash before each TeX command — never double them \
(`$\\hat{i}$` is correct in this prompt's escaping; the notes must contain \
the characters dollar, backslash, hat…, not two backslashes). Keep short \
symbols inline in the sentence; reserve `$$...$$` for multi-symbol \
equations you want on their own line.
- Write the notes as a document, not as a message to the reader. Never \
address the reader by name or in the second person.
- The document holds subject matter only. Never write your own process, \
limitations, or uncertainty into it: no notes about what the input did not \
cover, no placeholders for material that has not arrived, no remarks about \
what you could not determine or were unable to do. Anything you need to say \
about your own work goes in `chat_reply`.
- **Examples.** When a section teaches a concept, procedure, or formula and \
the lecture gave **no** concrete example, add a short original worked \
example under that section (or an `Examples` / `Worked example` heading). \
Invent numbers, inputs, and steps that illustrate the idea clearly — do \
not invent lecture facts that were never said. Prefer one solid example \
over several thin ones. If the lecture already included an example, do \
not invent another. Mention in `chat_reply` only when you added an \
example of your own.
"""

# Default is no diagram. An earlier "must include" rule produced diagrams
# for nearly every process-shaped paragraph; the bar is now "would a
# textbook draw this?", not "could this be drawn?".
_DIAGRAM_RULES = """\
## DIAGRAMS
Mermaid fenced code blocks render as real diagrams in this app. The \
**default is no diagram.** Most lecture segments need none — prose, \
bullets, and math are enough.

Draw a diagram only when **all** of the following are true:
- The material is the kind of thing conventionally taught *with* a \
diagram (e.g. a protocol handshake, a state machine with named states, \
a non-trivial branching decision tree, a multi-stage pipeline whose \
structure is the point).
- The diagram adds structure that prose alone would bury — not a \
restatement of a short numbered list.
- You would include at most **one** diagram for this entire update \
unless the input clearly covers two unrelated diagram-worthy systems.

Do **not** diagram:
- a short list of steps, definitions, or takeaways
- matrix/vector algebra, formulas, or unit-vector explanations
- composition-of-functions analogies or abstract mappings
- anything you could summarize in three bullets

When you do draw one, put it under the heading it belongs to, keep the \
prose alongside, and pick the type from the material:
- `sequenceDiagram` when parties exchange messages
- `stateDiagram-v2` when one thing moves between named states
- `flowchart TD` for a process or branching decision

The arrow forms are not interchangeable:
- flowchart uses `A --> B` and `A -->|label| B`
- sequenceDiagram uses `A->>B: label`
- stateDiagram-v2 uses `A --> B: label`

Never use `->>` inside a flowchart. Keep node ids short and alphanumeric, \
put text with spaces or punctuation inside brackets, and keep the diagram \
to the handful of nodes that carry the idea. You cannot produce images \
or screenshots.
"""

# Scopes the "no outside knowledge" rule in DEFAULT_BASE_INSTRUCTIONS. That
# rule protects transcribed lecture material, where invented content corrupts
# the record — but applied to a direct request it produced a refusal loop:
# the model started a document, declined to fill it for want of source
# material, and then wrote the same content happily once the user pasted its
# own chat answer back at it. Three turns for one. The router already
# separates these cases, so the rule can be scoped to the one it protects.
_USER_REQUEST_SCOPE = """\
## OUTSIDE KNOWLEDGE
The rule against adding outside knowledge governs transcribed lecture \
material, where inventing content corrupts the record. It does **not** \
govern explicit user requests.

When the user asks you to write, build, or explain something, use your own \
knowledge to do it properly. Never refuse a request for want of source \
material, and never leave a document you were asked to build empty or stubbed.

Say plainly in `chat_reply` when content came from you rather than from the \
lecture. The user needs to know which parts of their notes are a record of \
what was said and which are not. That attribution belongs in `chat_reply` \
and nowhere else — never annotate the notes document with it.
"""

DEFAULT_NOTES_INSTRUCTIONS = f"""\
# NOTES — EXTEND AN EXISTING DOCUMENT

---

## 1. TASK
This input has **ALREADY** been judged to warrant updating the notes. You \
are given the current notes document and the new input. Return three things.

## 2. `note_content`
The **FULL** updated notes document in well-formed Markdown (headings, \
bullet points, **bold** key terms).

- Integrate the new material coherently. Extend and refine the existing \
structure rather than appending a disconnected block, and put new material \
in the section it belongs to even when that section is higher up the document.

{_NOTES_WRITING_RULES}
{_DIAGRAM_RULES}
{_USER_REQUEST_SCOPE}

## 3. `chat_reply`
A **SHORT** conversational message (one or two sentences) describing what \
you changed. Name a `[?]` marking only when you actually made one this \
turn; when you marked nothing, say nothing about it — never report the \
absence. Never repeat the notes content here.

## 4. `title`
A short 3 to 6 word title for the conversation. If the current notes \
already have a title that still fits, return it unchanged. Only produce a \
new one when the document's actual subject has shifted, or when there was \
no usable title before.
"""

# Starting a document and extending one are different tasks. The extend
# prompt is dominated by preservation rules that are noise on a blank page,
# and its "keep headings stable" guidance actively discourages the model
# from committing to a structure.
DEFAULT_NEW_NOTES_INSTRUCTIONS = f"""\
# NOTES — START A NEW DOCUMENT

---

## 1. TASK
The notes document is empty and this input has been judged worth noting. \
You are starting the document. Return three things.

## 2. `note_content`
A new notes document in well-formed Markdown (headings, bullet points, \
**bold** key terms).

- Commit to a structure the rest of the lecture can extend. Group the \
material under headings rather than producing one flat list.
- When the input is lecture material, only cover what it actually contains: \
a short first input makes a short document, and you do not invent \
scaffolding for material that has not arrived yet. This does not apply when \
the user has asked you to build something — then you build it in full, from \
your own knowledge, in this turn.

{_NOTES_WRITING_RULES}
{_DIAGRAM_RULES}
{_USER_REQUEST_SCOPE}

## 3. `chat_reply`
A **SHORT** conversational message (one or two sentences) describing what \
you started. Name a `[?]` marking only when you actually made one this \
turn; when you marked nothing, say nothing about it — never report the \
absence. Never repeat the notes content here.

## 4. `title`
A short 3 to 6 word title based on the subject of this input.
"""

DEFAULT_CHAT_INSTRUCTIONS = """\
# CHAT — NOTES UNCHANGED

---

## 1. TASK
This input does **NOT** warrant changing the notes document. Respond in the \
chat only — the notes stay exactly as they are.

## 2. HOW TO REPLY
- If the input is a greeting, thanks, or other social remark, simply reply \
to it, briefly and naturally. Do not mention the notes at all: the user was \
not trying to add anything, so there is nothing to report. `hello` should \
get a greeting back, not a status update.
- If the user asked a question, answer it directly and helpfully, using the \
notes as context where relevant. Say plainly when the answer isn't in the \
notes and you're answering from general knowledge.
- You know the user's name and may use it here, sparingly, the way you \
would in conversation. This is the only place it belongs; the notes \
document never addresses the reader.
- Only when the user was evidently trying to add material and it fell short \
— a recording that cut out, an inaudible or truncated fragment — say so \
briefly, in your own words, phrased fresh each time. Never reach for a \
stock sentence.
- If the material was substantive but in a different subject area, say that \
plainly and offer to add it if they want it.
- If the input reads as a question about whether to change the notes, answer \
the question and offer to make the change, rather than making it.
- **Ask a clarifying question at most once, and never re-ask one the user \
has already answered.** If they have replied — even briefly, even just \
`yes` or `all` — treat it as answered. Act on the most reasonable reading \
and say in one clause which reading you took, so they can correct it. \
Re-confirming an answer the user already gave reads as stalling, and it \
costs them a whole turn to get back to where they were.

## 3. OUTPUT
Return `chat_reply` (one or two sentences) and `title` (a short 3 to 6 word \
title). Keep the existing title if the notes already have one that fits, \
otherwise base it on what's being discussed.
"""

# Compiles the profile form into a short description of the user. Every
# answer has to survive into the output — that's why the sentence budget
# is larger than it looks like it needs to be.
COMPILE_PROFILE_PROMPT = """\
# PROFILE COMPILER — USER DESCRIPTION ONLY

---

## 1. TASK
Rewrite the profile below as a short **biography of the user**. Output \
facts about the person. Nothing else.

## 2. WHAT TO WRITE
- Who they are (name, occupation/field, education, background level)
- What they use notes for (their stated purposes)
- What they want emphasized (list their choices as preferences they stated)
- Any free-text they added, paraphrased faithfully

## 3. WHAT NOT TO WRITE (reject these patterns)
- Imperative instructions to an assistant ("Write…", "Structure…", \
"Emphasize…", "Assume…", "Spell out…", "Pitch…")
- Note-writing strategy, depth, vocabulary, or reading-level guidance
- Predictions about how notes should be formatted or organized
- Second-person commands ("You should…")

## 4. FORM
- Third person only ("The user is…", "They are…", "Karl is…")
- 3–5 short sentences
- Cover every non-empty profile answer; do not invent missing ones
- No preamble, no title, no bullet list — plain prose only

## 5. EXAMPLE
Profile:
Name: Sam
Occupation or field: Biology undergrad
Education level: Undergraduate
Background level: Some background
Using notes for: Online courses, Exam prep
Wants emphasized: Definitions, Worked examples
Anything else: Midterm in 3 weeks

Bad (do not do this):
Write at an undergraduate level while assuming some background. Structure \
notes for exam prep. Emphasize definitions and worked examples.

Good (do this):
Sam is a biology undergraduate with some background in the field. They \
use notes for online courses and exam prep, and want definitions and \
worked examples emphasized. They have a midterm in 3 weeks.

## 6. PROFILE
{fields}
"""

# Hard cap applied on save. The sentence limit above is a request the model
# can ignore; this text rides on every generation call, so unbounded growth
# costs tokens forever and competes with lecture content for attention.
MAX_COMPILED_PROMPT_LEN = 700

class RouteDecision(BaseModel):
    update_notes: bool = Field(
        description="True only if this input should change the notes document."
    )
    reason: str = Field(
        description=(
            "One short sentence explaining the decision, usable in a reply "
            "to the user."
        )
    )


class NotesUpdate(BaseModel):
    note_content: str = Field(
        description=(
            "The full, updated notes document in Markdown, including all "
            "unchanged existing material reproduced verbatim."
        )
    )
    chat_reply: str = Field(
        description="A short reply for the chat thread, not the notes themselves."
    )
    title: str = Field(
        description=(
            "A short 3 to 6 word title for the conversation. Returns the "
            "existing title unchanged unless the subject has shifted."
        )
    )


class ChatReply(BaseModel):
    chat_reply: str = Field(description="A short reply for the chat thread.")
    title: str = Field(
        description=(
            "A short 3 to 6 word title for the conversation. Returns the "
            "existing title unchanged when one already fits."
        )
    )


class CompiledProfile(BaseModel):
    description: str = Field(
        description=(
            "Third-person biography of the user only — who they are and "
            "what they stated. Never imperative instructions for writing "
            "notes (no 'Write…', 'Structure…', 'Emphasize…', 'Assume…'). "
            "Empty string if the profile says nothing useful."
        )
    )


class TurnResult(NamedTuple):
    """What one turn produced. notes_updated distinguishes "the notes were
    deliberately left alone" from "the notes happen to be unchanged" — the
    caller must not overwrite the stored document when it's False."""

    note_content: str | None
    chat_reply: str
    title: str
    notes_updated: bool
    decision_reason: str


class GraphState(TypedDict):
    transcript: str
    history: list[dict[str, str]]
    current_note: str | None
    current_title: str | None
    update_notes: bool
    decision_reason: str
    note_content: str | None
    chat_reply: str
    title: str


# LaTeX commands starting with \t, \b, \f or \r (\tilde, \beta, \frac,
# \rho, ...) are a hazard in structured output: the model has to emit them
# JSON-escaped as "\\tilde", and when it under-escapes, JSON parsing turns
# "\t" into a literal TAB and the command arrives as "<TAB>ilde". The
# opposite failure is over-escaping: the note body contains "\\hat" and
# KaTeX treats \\ as a linebreak, stacking letters vertically. A third
# failure mode drops the backslash entirely or replaces it with a C0
# control character (seen: $\x05hat{i}$). Repair is scoped to math spans.
_MANGLED_ESCAPES = {"\t": "t", "\x08": "b", "\x0c": "f", "\r": "r"}
_MATH_SPAN = re.compile(r"\$\$.*?\$\$|\$[^$\n]*?\$", re.S)
_TEX_CMD = (
    r"hat|vec|bar|dot|tilde|widehat|overline|frac|sqrt|sum|prod|int|"
    r"partial|nabla|mathbf|mathrm|boldsymbol|operatorname|text|left|right|"
    r"cdot|times|ldots|cdots|infty|alpha|beta|gamma|delta|theta|lambda|"
    r"mu|pi|sigma|omega|mathbb|mathcal"
)
_CTRL_BEFORE_LETTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f](?=[a-zA-Z])")
_MISSING_BACKSLASH = re.compile(rf"(?<!\\)\b({_TEX_CMD})(?=[\s{{])")
# begin/end are included alongside _TEX_CMD because an over-escaped \\begin
# breaks an environment just as badly — and no matrix row starts with them.
_OVER_ESCAPED_CMD = re.compile(rf"\\\\({_TEX_CMD}|begin|end)\b")
# Models sometimes emit the Unicode escape for "$" (\u0024) instead of the
# character. That either stays as the literal six characters, or a five-
# digit slip (\u00024) decodes to STX+"4".
_MANGLED_DOLLAR = re.compile(
    r"\\u0024|\\U00000024|\\u00024|\x024",
    re.IGNORECASE,
)
_MANGLED_DOLLAR_SENTINEL = "\ue000"


def _repair_mangled_dollars(text: str) -> str:
    """Turn \\u0024 / STX+4 back into $, without doubling real delimiters.

    Models often write $\\u0024x\\u0024$ — a real $ pair wrapping unicode
    escapes for the same character. Replacing escapes with $ naively yields
    $$x$$, so drop sentinels that sit next to an existing $.
    """
    t = _MANGLED_DOLLAR.sub(_MANGLED_DOLLAR_SENTINEL, text)
    s = _MANGLED_DOLLAR_SENTINEL
    t = t.replace(f"${s}", "$").replace(f"{s}$", "$")
    t = t.replace(s, "$")
    # Odd-length runs of $ from overlapping repairs → one $; even → $$.
    def collapse(m: re.Match[str]) -> str:
        n = len(m.group(0))
        return "$$" if n % 2 == 0 else "$"

    return re.sub(r"\${3,}", collapse, t)


def _repair_latex_escapes(text: str) -> str:
    text = _repair_mangled_dollars(text)

    def fix(match: re.Match[str]) -> str:
        span = match.group(0)
        for char, letter in _MANGLED_ESCAPES.items():
            # Only when a letter follows — that's what makes it a command
            # rather than incidental whitespace.
            span = re.sub(re.escape(char) + r"(?=[a-zA-Z])", "\\\\" + letter, span)
        # Other C0 controls before a letter (e.g. \x05hat) → backslash.
        span = _CTRL_BEFORE_LETTER.sub(r"\\", span)
        # $hat{i}$ → $\hat{i}$ for known commands.
        span = _MISSING_BACKSLASH.sub(r"\\\1", span)
        # \\hat → \hat (repeat for \\\hat etc.). Only when a known command
        # follows: matching any letter ate matrix row separators, because the
        # \\ in \begin{bmatrix}a&b\\c&d\end{bmatrix} is a row break and \c is
        # not a command. Rows starting with a digit were unaffected, which is
        # why numeric matrices rendered and symbolic ones did not.
        while True:
            collapsed = _OVER_ESCAPED_CMD.sub(r"\\\1", span)
            if collapsed == span:
                break
            span = collapsed
        return span

    return _MATH_SPAN.sub(fix, text)


def _resolve_llm(settings: Settings, model: str | None = None) -> BaseChatModel:
    """Returns a configured chat model, raising a 503 if the provider's API
    key isn't set. Defaults to settings.llm_model (notes/chat); pass
    settings.routing_llm_model for the classify router."""
    model_id = model or settings.llm_model
    provider = model_id.split(":", 1)[0]
    key_field = _PROVIDER_KEY_FIELDS.get(provider)
    api_key = getattr(settings, key_field, "") if key_field else ""
    if not api_key:
        logger.warning("LLM provider %r is not configured (missing API key)", provider)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM provider '{provider}' is not configured (missing API key)",
        )

    # init_chat_model's automatic provider auth looks up a standard env var
    # per provider (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...); make sure it's
    # actually set even when running outside Docker (where .env is only read
    # by pydantic-settings, not exported to the process environment).
    os.environ.setdefault(f"{provider.upper()}_API_KEY", api_key)

    return init_chat_model(model_id)


async def _user_profile(db: Session, settings: Settings) -> str:
    """The compiled personal context, or "" when there is none.

    If the profile has answers but no compiled text, an earlier compilation
    failed — try once more here. It must never block the note job: any
    failure just means notes generate without personalization, which is
    fine. Notes that fail to generate are not.
    """
    profile = db.scalar(select(UserProfile))
    if profile is None:
        return ""

    compiled = (profile.compiled_prompt or "").strip()
    if compiled:
        return compiled

    # Nothing to compile from — the user simply hasn't filled it in.
    if not format_profile_fields(profile.fields or {}, profile.name).strip():
        return ""

    try:
        result = await compile_profile(profile.fields or {}, profile.name, settings)
    except Exception:
        logger.exception("Lazy profile compilation failed; generating without personalization")
        result = None

    if result:
        profile.compiled_prompt = result
        profile.compile_failed_at = None
    else:
        profile.compile_failed_at = datetime.now(UTC)
    db.commit()
    return (result or "").strip()


def _build_routing_prompt() -> str:
    return f"{DEFAULT_BASE_INSTRUCTIONS}\n\n---\n\n{DEFAULT_ROUTING_INSTRUCTIONS}"


# The compiled profile is a description of the user, pasted as context —
# only this guard. Without it the model treats the block as material to
# write about: an earlier build cheerfully added a "My note-taking style"
# section to the user's actual lecture notes.
_PERSONALIZATION_WARNING = (
    "This describes the reader. Never mention it, restate it, or add it "
    "as a section in the notes. Do not address the reader by name or "
    "occupation in the notes document. However, this is allowed in the chat"
)


def _build_notes_prompt(user_profile: str, starting_new: bool) -> str:
    """Starting a document and extending one are different tasks, so they get
    different instructions — see DEFAULT_NEW_NOTES_INSTRUCTIONS."""
    instructions = DEFAULT_NEW_NOTES_INSTRUCTIONS if starting_new else DEFAULT_NOTES_INSTRUCTIONS
    prompt = f"{DEFAULT_BASE_INSTRUCTIONS}\n\n---\n\n{instructions}"
    if user_profile:
        prompt += f"\n\n---\n\n{user_profile}\n{_PERSONALIZATION_WARNING}"
    return prompt


def _build_chat_prompt(user_profile: str) -> str:
    """Chat replies explain subject matter, so the profile still applies —
    an answer pitched wrong is unhelpful whichever branch it comes from."""
    prompt = f"{DEFAULT_BASE_INSTRUCTIONS}\n\n---\n\n{DEFAULT_CHAT_INSTRUCTIONS}"
    if user_profile:
        prompt += f"\n\n---\n\n{user_profile}\n{_PERSONALIZATION_WARNING}"
    return prompt


def _note_context(state: GraphState) -> str:
    """Current title + notes, as shown to every node. The title is included
    because the prompts ask the model to reuse it unless the subject has
    genuinely shifted — it can't do that without seeing it."""
    parts = []
    title = (state.get("current_title") or "").strip()
    # "New conversation" is the placeholder, not a real title to preserve.
    if title and title != "New conversation":
        parts.append(f"Current conversation title: {title}")
    parts.append(
        f"Current notes document:\n\n{state['current_note']}"
        if state["current_note"]
        else "No notes document exists yet for this conversation."
    )
    return "\n\n".join(parts)


def _history_messages(history: list[dict[str, str]]) -> list[AIMessage | HumanMessage]:
    messages: list[AIMessage | HumanMessage] = []
    for turn in history:
        if turn["role"] == "assistant":
            messages.append(AIMessage(turn["content"]))
        else:
            messages.append(HumanMessage(turn["content"]))
    return messages


def _classify(state: GraphState, settings: Settings) -> GraphState:
    model = _resolve_llm(settings, settings.routing_llm_model).with_structured_output(RouteDecision)
    messages = [
        SystemMessage(_build_routing_prompt()),
        *_history_messages(state["history"]),
        HumanMessage(f"{_note_context(state)}\n\n---\n\nNew input:\n{state['transcript']}"),
    ]
    result: RouteDecision = model.invoke(messages)
    logger.info(
        "Routing decision: update_notes=%s (%s)",
        result.update_notes,
        result.reason,
    )
    return {**state, "update_notes": result.update_notes, "decision_reason": result.reason}


def _write_notes(state: GraphState, settings: Settings, user_profile: str) -> GraphState:
    model = _resolve_llm(settings).with_structured_output(NotesUpdate)
    starting_new = not (state["current_note"] or "").strip()
    messages = [
        SystemMessage(_build_notes_prompt(user_profile, starting_new)),
        *_history_messages(state["history"]),
        HumanMessage(f"{_note_context(state)}\n\n---\n\nNew input:\n{state['transcript']}"),
    ]
    result: NotesUpdate = model.invoke(messages)
    note_content = _repair_latex_escapes(result.note_content)
    logger.info(
        "Notes %s (%d chars)", "started" if starting_new else "updated", len(note_content)
    )
    return {
        **state,
        "note_content": note_content,
        "chat_reply": result.chat_reply,
        "title": result.title,
    }


def _answer_chat(state: GraphState, settings: Settings, user_profile: str) -> GraphState:
    model = _resolve_llm(settings).with_structured_output(ChatReply)
    messages = [
        SystemMessage(_build_chat_prompt(user_profile)),
        *_history_messages(state["history"]),
        HumanMessage(
            f"{_note_context(state)}\n\n---\n\nNew input:\n{state['transcript']}"
            f"\n\n---\n\nInternal routing note, context for you only — do not "
            f"repeat or explain it unless it is genuinely what the user "
            f"needs to hear: {state['decision_reason']}"
        ),
    ]
    result: ChatReply = model.invoke(messages)
    logger.info("Chat-only reply (notes untouched)")
    # Pass the current notes straight through — the caller checks
    # notes_updated and won't persist this, but keeping it accurate means
    # the response still carries the real document for the UI.
    return {
        **state,
        "note_content": state["current_note"],
        "chat_reply": result.chat_reply,
        "title": result.title,
    }


def build_graph(settings: Settings, user_profile: str):
    """classify -> (write_notes | answer_chat) -> END

    Splitting the decision from the writing is the point: the router can say
    "no" without ever being handed a contract that requires producing a
    document.
    """
    graph = StateGraph(GraphState)
    graph.add_node("classify", lambda state: _classify(state, settings))
    graph.add_node("write_notes", lambda state: _write_notes(state, settings, user_profile))
    graph.add_node("answer_chat", lambda state: _answer_chat(state, settings, user_profile))

    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: "write_notes" if state["update_notes"] else "answer_chat",
        {"write_notes": "write_notes", "answer_chat": "answer_chat"},
    )
    graph.add_edge("write_notes", END)
    graph.add_edge("answer_chat", END)
    return graph.compile()


async def generate_response(
    transcript: str,
    history: list[dict[str, str]],
    current_note: str | None,
    current_title: str | None,
    settings: Settings,
    db: Session,
) -> TurnResult:
    # Read the DB-backed profile prompt up front rather than inside a node —
    # the graph runs its nodes in a worker thread (LangGraph invokes sync
    # callables via run_in_executor), and a SQLAlchemy Session isn't safe
    # across threads.
    graph = build_graph(settings, await _user_profile(db, settings))
    result = await graph.ainvoke(
        {
            "transcript": transcript,
            "history": history,
            "current_note": current_note,
            "current_title": current_title,
            "update_notes": False,
            "decision_reason": "",
            "note_content": current_note,
            "chat_reply": "",
            "title": "",
        }
    )
    return TurnResult(
        note_content=result["note_content"],
        chat_reply=result["chat_reply"],
        title=result["title"],
        notes_updated=result["update_notes"],
        decision_reason=result["decision_reason"],
    )


def format_profile_fields(fields: dict, name: str = "") -> str:
    """Every form answer as labeled lines for the compiler."""
    def render(value: object) -> str:
        # Tolerates both list and string, since notes_purpose was a single
        # string before it became multi-select.
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if str(v).strip())
        return str(value or "")

    labeled = [
        ("Name", name),
        ("Occupation or field", fields.get("occupation", "")),
        ("Education level", fields.get("education_level", "")),
        ("Background level", fields.get("background_level", "")),
        ("Using notes for", fields.get("notes_purpose")),
        ("Wants emphasized", fields.get("emphasize")),
        ("Anything else", fields.get("extra", "")),
    ]
    return "\n".join(
        f"{label}: {rendered}"
        for label, value in labeled
        if (rendered := render(value).strip())
    )


async def compile_profile(fields: dict, name: str, settings: Settings) -> str | None:
    """Compiles the form answers into a short description of the user.
    Returns None when there's nothing worth compiling, so the caller
    stores NULL."""
    rendered = format_profile_fields(fields, name)
    if not rendered.strip():
        return None
    model = _resolve_llm(settings).with_structured_output(CompiledProfile)
    logger.info("Compiling personal context layer")
    result: CompiledProfile = await model.ainvoke(
        [HumanMessage(COMPILE_PROFILE_PROMPT.format(fields=rendered))]
    )
    compiled = result.description.strip()[:MAX_COMPILED_PROMPT_LEN].strip()
    logger.info("Personal context compiled (%d chars)", len(compiled))
    return compiled or None
