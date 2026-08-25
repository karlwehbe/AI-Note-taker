# Flows

End-to-end diagrams for the major paths through AI Note Taker. Each section
names the real files and functions involved, so a diagram can be traced
straight into the code.

- [1. System overview](#1-system-overview)
- [2. Data model](#2-data-model)
- [3. Live recording → notes](#3-live-recording--notes)
- [4. Recording lifecycle (state machine)](#4-recording-lifecycle-state-machine)
- [5. Recording survives navigation](#5-recording-survives-navigation)
- [6. Draft autosave & crash recovery](#6-draft-autosave--crash-recovery)
- [7. File upload → notes](#7-file-upload--notes)
- [8. Typed message → notes](#8-typed-message--notes)
- [9. User profile → personalized notes](#9-user-profile--personalized-notes)
- [10. Routing: whether to touch the notes at all](#10-routing-whether-to-touch-the-notes-at-all)
- [11. Conversation creation & cleanup](#11-conversation-creation--cleanup)
- [12. Rendering the notes document](#12-rendering-the-notes-document)

---

## 1. System overview

```mermaid
flowchart LR
  subgraph Browser["Browser (React + TanStack Router)"]
    UI["Sidebar / routes<br/>index.tsx, c.$conversationId.tsx"]
    RC["RecordingProvider<br/>lib/recording-context.tsx<br/><i>above the router</i>"]
    CC["ChatComposer<br/>components/chat-composer.tsx"]
    API["api client<br/>lib/api.ts"]
  end

  subgraph Server["FastAPI (server/app)"]
    CONV["/conversations<br/>api/conversations.py"]
    WS["/ws/transcribe<br/>api/live_transcribe.py"]
    PR["/profile<br/>api/profile.py"]
    GRAPH["notes_graph.py<br/>LangGraph: classify → notes/chat"]
    TR["transcription.py<br/>batch STT"]
  end

  DB[("Postgres<br/>conversations, messages,<br/>user_profiles")]
  DG["Deepgram<br/>live + batch STT"]
  LLM["OpenAI<br/>via init_chat_model<br/><i>two models: LLM_MODEL + ROUTING_LLM_MODEL</i>"]

  UI --> CC
  RC -.->|shared recording state| CC
  CC --> API
  API -->|REST| CONV
  API -->|REST| PR
  RC -->|WebSocket audio| WS
  WS <-->|proxied stream| DG
  CONV --> TR --> DG
  CONV --> GRAPH --> LLM
  PR --> GRAPH
  CONV --> DB
  PR --> DB
  GRAPH -->|"reads compiled_prompt"| DB
```

The Deepgram API key never reaches the browser — `/ws/transcribe` proxies the
live stream server-side (`live_transcribe.py`).

---

## 2. Data model

```mermaid
erDiagram
  CONVERSATIONS ||--o{ MESSAGES : has
  CONVERSATIONS {
    uuid id PK
    string title "AI-generated on first turn"
    text note_content "the evolving notes document"
    text draft_transcript "autosaved mid-recording, cleared on send"
    timestamp created_at
    timestamp updated_at
  }
  MESSAGES {
    uuid id PK
    uuid conversation_id FK
    string role "user | assistant"
    text content
    string filename "set for audio; 'recording.webm' for live"
    timestamp created_at
  }
  USER_PROFILES {
    uuid id PK
    string name "compiled in; usable in chat, never in the notes"
    jsonb fields "occupation, level, purpose, emphasize, skip, extra"
    text compiled_prompt "NULL = no personal layer"
    boolean is_edited "true once hand-rewritten"
    timestamp compile_failed_at "NULL unless a compile failed"
    timestamp updated_at
  }
```

`USER_PROFILES` is intentionally unrelated to conversations — it applies
globally (no auth/per-user scoping in this build) and is effectively a
single row: saving replaces the previous value.

`compiled_prompt` is the *only* prompt text in the database. Everything
else the model is told lives as plain constants in
`services/notes_graph.py`, versioned with the code that reads it. That
column is the exception: it is written by an LLM at runtime from user
input, so the code can't hold it.

---

## 3. Live recording → notes

The main path. Note the two separate audio journeys: chunks stream to
Deepgram live for the on-screen transcript, **and** accumulate into a blob
that's uploaded on send.

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant CC as ChatComposer
  participant RC as RecordingContext
  participant MR as MediaRecorder
  participant WS as /ws/transcribe
  participant DG as Deepgram
  participant API as /conversations
  participant G as notes_graph
  participant DB as Postgres

  U->>CC: click record (mic / system audio)
  CC->>RC: startRecording(conversationId, source)
  RC->>RC: getUserMedia / getDisplayMedia
  alt no conversation yet (new-chat page)
    RC->>API: POST /conversations
    API->>DB: INSERT conversation ("New conversation")
    Note over RC: appears in sidebar immediately
  end
  RC->>RC: start Web Audio analyser (waveform)
  RC->>WS: open WebSocket
  RC->>RC: await socket OPEN
  Note over RC,WS: must wait — chunk #1 carries the container<br/>header every later chunk needs to decode
  RC->>MR: recorder.start(250)

  loop every 250ms while recording
    MR-->>RC: ondataavailable(chunk)
    RC->>RC: push to chunks[] (for the final blob)
    RC->>WS: send(chunk)
    WS->>DG: relay audio
    DG-->>WS: interim / final transcript
    WS-->>RC: {transcript, is_final}
    RC-->>CC: live transcript updates
    alt is_final
      RC->>API: PATCH /{id}/draft (autosave)
      API->>DB: UPDATE draft_transcript
    end
  end

  U->>CC: click send
  CC->>RC: stopAndFinalize()
  RC->>MR: stop()
  MR-->>RC: onstop → build Blob
  RC-->>CC: {blob, transcript, conversationId}

  alt transcript is empty (silence)
    CC->>CC: reject client-side, show error
    CC->>API: DELETE conversation (only if created for this recording)
    Note over CC: no server round trip, no phantom message
  else has speech
    CC->>API: POST /{id}/messages (blob + transcript)
    API->>DB: INSERT user message
    API->>G: generate_response(...)
    G->>DB: read compiled_prompt
    G->>G: classify → write_notes | answer_chat
    G-->>API: chat_reply, title, notes_updated
    API->>DB: INSERT assistant msg, clear draft_transcript,<br/>set title (first turn), UPDATE note_content<br/><i>only if notes_updated</i>
    API-->>CC: MessageTurn
    CC-->>U: message + notes panel update
  end
```

Because the transcript is captured live, the send path passes it along and
the server **skips** a redundant batch transcription (`send_message` only
calls `transcribe_audio` when `transcript is None`).

---

## 4. Recording lifecycle (state machine)

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> Acquiring: click record
  Acquiring --> Idle: permission denied / no device<br/>(friendlyRecordingError)
  Acquiring --> Recording: stream + socket ready

  Recording --> Paused: pause (flushes draft)
  Paused --> Recording: resume
  note right of Paused
    Same chunks[] array continues —
    pause never splits the recording
  end note

  Recording --> Finalizing: send
  Paused --> Finalizing: send
  Finalizing --> Idle: empty transcript → error<br/>(+ delete throwaway conversation)
  Finalizing --> Sending: has speech
  Sending --> Idle: turn saved

  Recording --> Idle: clear (discardRecording)
  Paused --> Idle: clear (discardRecording)
  note left of Idle
    discardRecording also deletes the
    conversation if it was created just
    for this recording and never sent
  end note
```

---

## 5. Recording survives navigation

The engine lives in `RecordingProvider` at the **app root, above
`<Outlet />`** — so switching routes never unmounts it. Only the *view*
changes.

```mermaid
flowchart TD
  START(["Recording in progress"]) --> Q{"Which page is the<br/>user looking at?"}
  Q -->|"the recording's own conversation"| INLINE["Inline in ChatComposer<br/>live transcript, pause, clear, send"]
  Q -->|"any other page"| WIDGET["Floating RecordingWidget<br/>bottom-left: waveform, timer, pause<br/><i>no send — that happens in the chat</i>"]
  WIDGET -->|click widget| NAV["navigate to that conversation"] --> INLINE
  INLINE -->|navigate away| WIDGET

  subgraph decides["isHostViewingRecording (recording-context.tsx)"]
    D1["ChatComposer reports the page it's showing<br/>via reportViewingConversation()"]
    D2["compare against the session's<br/>conversation id"]
    D1 --> D2
  end
  Q -.-> decides
```

`nullHostRetired` handles one edge case: a recording started on `/` must not
re-attach itself to `/` later (clicking "New chat" after visiting another
conversation) — otherwise a fresh new-chat page would resurrect the old
recording's transcript.

---

## 6. Draft autosave & crash recovery

Protects a long recording from being lost before the user ever hits send.

```mermaid
sequenceDiagram
  autonumber
  participant RC as RecordingContext
  participant API as /conversations
  participant DB as Postgres
  participant CC as ChatComposer

  Note over RC: while recording
  loop each finalized Deepgram segment (+ on pause)
    RC->>API: PATCH /{id}/draft {transcript}
    API->>DB: UPDATE conversations.draft_transcript
  end

  Note over RC,DB: 💥 tab crash / reload / navigate away

  CC->>API: GET /conversations/{id}
  API-->>CC: draft_transcript
  CC->>CC: restore into composer (restoredDraft)
  Note over CC: user can send it as-is, or hit record<br/>to continue on top (seedTranscript)

  Note over CC,DB: on a successful send
  API->>DB: clear draft_transcript
```

Guards worth knowing (both exist because a late Deepgram result could
otherwise resurrect text that was deliberately discarded):

- `allowDraftSaveRef` — flipped off the moment a send or discard begins.
- `ws.onmessage = null` before closing the socket, since `close()` doesn't
  cancel a message already in flight.

---

## 7. File upload → notes

No live transcript exists, so the **server** transcribes via Deepgram's batch
REST API.

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant CC as ChatComposer
  participant API as /conversations
  participant TR as transcription.py
  participant DG as Deepgram REST
  participant G as notes_graph
  participant DB as Postgres

  U->>CC: attach audio file → send
  CC->>API: POST /{id}/messages (file, no transcript)
  API->>API: size checks (≤25MB, non-empty)
  API->>TR: transcribe_audio(bytes)
  TR->>DG: POST /v1/listen
  DG-->>TR: transcript
  TR-->>API: text
  alt transcript blank (no speech)
    API-->>CC: 400 "No speech detected"
  else
    API->>DB: INSERT user message (filename kept)
    API->>G: generate_response(...)
    G->>DB: read compiled_prompt
    G-->>API: chat_reply, title, notes_updated
    API->>DB: INSERT assistant msg, title,<br/>notes only if notes_updated
    API-->>CC: MessageTurn
  end
```

In the chat, an uploaded file renders as a **file chip** (filename + expand
chevron) rather than raw transcript text — `isFileAttachment()` in
`message-bubble.tsx` distinguishes a real upload from a live recording by
checking the filename isn't `recording.webm`.

---

## 8. Typed message → notes

The shortest path — no audio anywhere.

```mermaid
flowchart LR
  A["User types<br/>+ Enter / send"] --> B["finalizeSendText()"]
  B --> C["optimistic bubble<br/>onPendingMessage()"]
  B --> D["POST /{id}/messages<br/>(transcript form field only)"]
  D --> E["generate_response()"]
  E --> F{"instruction or<br/>question?"}
  F -->|"'add a section on X'"| G["notes edited"]
  F -->|"'what is X?'"| H["notes unchanged,<br/>answered in chat_reply"]
  G --> I["UPDATE note_content"]
  H --> I
  I --> J["MessageTurn → chat + notes panel"]
```

---

## 9. User profile → personalized notes

A short form, compiled by an LLM into a few sentences **describing the
user** from their answers. That text rides along on every note/chat
prompt as context — `compiled_prompt` is pasted into the system prompt
verbatim, never re-interpreted. The compiler does not invent note-writing
strategy; it only restates who the user is and what they said.

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant D as ProfileDialog
  participant PR as /profile
  participant G as notes_graph
  participant LLM as OpenAI
  participant DB as Postgres

  U->>D: click the profile row (sidebar bottom)
  D->>PR: GET /profile
  PR-->>D: fields + compiled_prompt + is_edited
  U->>D: name, occupation, level, purpose,<br/>emphasize, skip, free text
  D->>PR: PUT /profile
  PR->>DB: COMMIT the answers
  Note over PR,DB: committed before the LLM is touched —<br/>a compile failure can never roll back<br/>what the user typed

  alt answers changed
    PR->>G: compile_profile(fields, name)
    G->>LLM: describe the user from the form,<br/>covering every answer given
    alt success
      LLM-->>G: description
      G-->>PR: text (truncated to 700 chars)
      PR->>DB: compiled_prompt = text,<br/>compile_failed_at = NULL
    else failure
      G-->>PR: raises
      PR->>DB: compiled_prompt = NULL,<br/>compile_failed_at = now()
      Note over PR: logged, but still HTTP 200 —<br/>the save itself succeeded
    end
  end

  PR-->>D: 200 with fields + compiled_prompt
  D-->>U: description shown, editable
```

Compilation runs **inside** the request and returns with it, so the response
is complete: no polling, and nothing appears later out of nowhere. It only
runs when an answer actually changed, so re-saving an untouched form costs
nothing (measured: 46ms vs ~1.5s).

### Three states, told apart by two columns

`compiled_prompt` alone is ambiguous — null could mean "never filled in" or
"we tried and it broke". `compile_failed_at` is what separates them.

```mermaid
flowchart LR
  A["compiled_prompt: NULL<br/>compile_failed_at: NULL"] --> A2["never filled in<br/><i>no personal layer, nothing to fix</i>"]
  B["compiled_prompt: NULL<br/>compile_failed_at: set"] --> B2["tried and failed<br/><i>inline notice + Retry in the dialog</i>"]
  C["compiled_prompt: text<br/>compile_failed_at: NULL"] --> C2["active<br/><i>pasted into the prompt</i>"]
```

### Lazy retry

If a note job runs while the profile has answers but no compiled text, an
earlier compile failed. It tries once more, then gets out of the way.

```mermaid
flowchart TD
  MSG["a message is sent"] --> U["_user_profile(db, settings)"]
  U --> Q{"compiled_prompt set?"}
  Q -->|yes| USE["use it"]
  Q -->|no| Q2{"any answers<br/>to compile from?"}
  Q2 -->|"no — never filled in"| SKIP["return '' — no personal layer"]
  Q2 -->|yes| TRY["attempt compilation once"]
  TRY -->|success| STORE["store it, clear compile_failed_at"] --> USE
  TRY -->|"fails again"| STAMP["re-stamp compile_failed_at,<br/>return ''"] --> SKIP
```

The rule that governs all of it: **notes without personalization are fine;
notes that fail to generate are not.** A compile failure never raises into
the note job, and `_build_notes_prompt` simply omits the block when the
string is empty — no special casing anywhere else.

### Protecting a hand-edited prompt

The compiled text is shown in an editable textarea, because an invisible
profile feels like it does nothing. Editing it sets `is_edited`, after which
the app won't silently overwrite your wording.

| Action | Result |
|---|---|
| Save, never hand-edited | Recompiles if any answer changed |
| Save, after hand-editing | Asks first — **Keep mine** saves the answers but skips the recompile |
| Regenerate | Always recompiles, no confirmation |
| Editing the textarea | Saves on blur, marks `is_edited` |

Two subtleties worth knowing. `name` is a column of its own rather than part
of `fields`, so change detection compares **both** — otherwise renaming
yourself would save but leave the old name baked into the description.
And Save waits on any in-flight blur-save before reading `is_edited`, since
clicking Save straight out of the textarea fires both at once.

## 10. Routing: whether to touch the notes at all

Each turn runs a two-step LangGraph: a router decides *whether* the notes
should change, and only then does a second call write them.

```mermaid
flowchart TD
  IN["New input<br/>(transcript / typed message)"] --> C["<b>classify</b><br/>_build_routing_prompt()<br/>→ RouteDecision{update_notes, reason}"]
  C --> Q{"update_notes?"}

  Q -->|"true"| N{"notes empty?"}
  N -->|yes| WN["<b>write_notes</b> · starting<br/>DEFAULT_NEW_NOTES_INSTRUCTIONS<br/><i>commit to a structure</i>"]
  N -->|no| W["<b>write_notes</b> · extending<br/>DEFAULT_NOTES_INSTRUCTIONS<br/><i>preserve, integrate, keep headings stable</i>"]
  Q -->|"false"| A["<b>answer_chat</b><br/>_build_chat_prompt()<br/>→ ChatReply{chat_reply, title}"]

  WN --> R1
  W --> R1["notes_updated = true<br/>→ document saved"]
  A --> R2["notes_updated = false<br/>→ document left untouched"]
```

Both note branches share one set of writing rules (`_NOTES_WRITING_RULES`);
only the framing differs. Starting a document wants commitment to a
structure, while extending one is dominated by preservation rules that are
noise on a blank page — and its "keep existing headings stable" guidance
actively discourages committing to any.

**Why two calls instead of one.** The router's schema (`RouteDecision`) has
no `note_content` field, so it *cannot* write notes even if the model wants
to. A single call given a "return the full updated document" contract will
essentially always return one — which is how filler like `.` or `thanks`
used to rewrite the notes.

The router says **no** when there isn't enough substance to build notes
from, when the input is a question meant for the chat, or when substantive
content belongs to a genuinely different domain (it offers in chat instead
of silently grafting it on). Relatedness is judged at the level of the
*course*, not the subtopic — a lecture constantly introduces new material,
so a new chapter or definition is still related. Explicit instructions —
*"add a section on X"* — always win, so changing topic on purpose works.

The prompts also tell the model its input is **ASR output**: expect missing
punctuation, false starts, and misheard technical terms, and read through
those artifacts rather than treating them as content. Unresolvable terms get
marked `[?]` in the document.

`conversations.py` only persists notes when `notes_updated` is true, and
always returns the *stored* document, so a chat-only turn can't blank the
notes panel client-side.

### What each prompt is built from

```mermaid
flowchart LR
  subgraph code["notes_graph.py constants"]
    BASE["DEFAULT_BASE_INSTRUCTIONS"]
    ROUTE["DEFAULT_ROUTING_INSTRUCTIONS"]
    NOTES["DEFAULT_NOTES_INSTRUCTIONS<br/>DEFAULT_NEW_NOTES_INSTRUCTIONS"]
    CHATI["DEFAULT_CHAT_INSTRUCTIONS"]
  end
  PROF[("user_profiles<br/>.compiled_prompt")]

  BASE --> P1["routing prompt<br/><i>ROUTING_LLM_MODEL</i>"]
  ROUTE --> P1
  BASE --> P2["notes prompt<br/><i>LLM_MODEL</i>"]
  NOTES --> P2
  PROF -.->|"only if filled in"| P2
  BASE --> P3["chat prompt<br/><i>LLM_MODEL</i>"]
  CHATI --> P3
  PROF -.->|"only if filled in"| P3
```

Note which arrows are missing. The **router** gets no personalization at all
— whether an input deserves a note change has nothing to do with who the
user is. The **profile** reaches notes *and* chat, because an explanation
pitched at the wrong level is unhelpful whichever branch produced it.

The router also runs on its own model (`ROUTING_LLM_MODEL`, resolved
separately in `_resolve_llm`). It answers a yes/no question against a small
schema, so it doesn't need the model that writes the notes — and keeping it
cheap means the per-turn cost of *refusing* to write notes stays near zero.

That containment is what makes personalization safe: user-supplied content
can never reach the output contracts the code depends on.

---

## 11. Conversation creation & cleanup

A conversation is created **eagerly** (the moment recording starts) so the
transcript has somewhere to persist — which means the throwaway cases need
explicit cleanup.

```mermaid
flowchart TD
  START(["User on / (new chat)"]) --> ACT{"action"}
  ACT -->|"click record"| EAGER["POST /conversations<br/><i>immediately</i>"]
  ACT -->|"type / attach + send"| LAZY["created during send"]

  EAGER --> REC["recording, autosaving draft"]
  REC --> OUT{"how does it end?"}
  OUT -->|"send with speech"| KEEP["kept · titled by AI · notes written"]
  OUT -->|"send, silence"| DEL1["deleted<br/>(stopAndSend)"]
  OUT -->|"clear / discard"| DEL2["deleted<br/>(discardRecording)"]
  OUT -->|"deleted from sidebar<br/>mid-recording"| DEL3["recording stopped first,<br/>then deleted"]

  LAZY --> KEEP

  KEEP --> TITLE{"first turn?"}
  TITLE -->|yes| SET["title = AI-generated<br/>(replaces 'New conversation')"]
  TITLE -->|no| UNCHANGED["title left alone"]
```

Every delete path also stops any recording attached to that conversation
first — otherwise its autosaves would 404 in a loop and the floating widget
would point at a conversation that no longer exists.

---

## 12. Rendering the notes document

`note_content` is Markdown written by a model, which makes it *untrusted*
input as far as the browser is concerned. One shared `<Markdown>` component
(`components/markdown.tsx`) renders both the notes panel and assistant chat
messages, so the two can't drift apart.

```mermaid
flowchart TD
  SRC["note_content (Markdown from the model)"] --> NM["normalizeMath()<br/><i>rewrite \\( \\) and \\[ \\] to $ / $$</i>"]
  NM --> RG["remark-gfm + remark-math<br/><i>tables, task lists, math nodes</i>"]
  RG --> RAW["rehype-raw<br/><i>parse inline HTML for real</i>"]
  RAW --> SAN["rehype-sanitize<br/><i>strip anything unsafe</i>"]
  SAN --> KTX["rehype-katex<br/><i>render equations</i>"]
  KTX --> OUT["React elements"]
  OUT --> PRE{"is this &lt;pre&gt; a<br/>```mermaid fence?"}
  PRE -->|no| CODE["normal code block"]
  PRE -->|yes| MD["MermaidDiagram<br/>components/mermaid-diagram.tsx"]
```

Two orderings in there are load-bearing:

- **raw → sanitize → katex.** `rehype-raw` has to parse the model's HTML
  before `rehype-sanitize` can clean it. KaTeX runs *last* because it injects
  its own classed markup, and the sanitize schema doesn't allow `className` —
  running it earlier would strip every rendered equation.
- **`pre`, not `code`.** The mermaid fence is intercepted on the `<pre>`
  element. Returning a `<div>` from the `code` component would nest flow
  content inside an element that only accepts phrasing content.

### Diagrams

Mermaid is imported **dynamically** — it's the largest dependency in the app
and most notes contain none, so it stays out of the main bundle. Diagram
source is model-generated and therefore often malformed, so `parse()` runs
before `render()` and every failure falls back to showing the source as an
ordinary code block. A broken diagram must never blank a set of notes the
user just recorded an hour of lecture for.

On the server, `_DIAGRAM_RULES` is its own section of the notes prompt rather
than a bullet among the writing rules. Position alone made no measurable
difference; what moved the model was making the trigger an **imperative**.
Drawing a diagram is discretionary, and a small model resolves discretion
toward doing less — so the four shapes that warrant one (ordered process,
state machine, message exchange, branching decision) are named as a
requirement, along with which diagram type each maps to and the arrow syntax
that belongs to it.
