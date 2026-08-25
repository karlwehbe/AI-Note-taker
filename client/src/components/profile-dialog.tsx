// "Your profile" — the personal context layer. A short form, compiled
// server-side by an LLM into a short description of the user that rides
// along on every note/chat prompt as context about who's reading.
//
// The compiled text is shown and editable on purpose: an invisible profile
// feels like it does nothing, and being able to read (and fix) what the
// assistant was told is what makes the feature trustworthy. Editing it by
// hand sets is_edited server-side, after which changing a form answer asks
// before overwriting your wording.
import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { AlertCircle, Loader2, RefreshCw, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { api } from "@/lib/api"
import type { ProfileFields, UserProfileState } from "@/lib/api"

const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
const FIELD =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-[var(--muted)]"

const EMPTY_FIELDS: ProfileFields = {
  occupation: "",
  background_level: "",
  education_level: "",
  notes_purpose: [],
  emphasize: [],
  extra: "",
}

// Deliberately domain-neutral. An earlier set led with "Code", which skewed
// the whole form toward programming — these have to work for medicine, law,
// history and everything else just as well.
const BACKGROUND_LEVELS = ["New to this", "Some background", "Comfortable", "Expert"]
// Formal education, distinct from background level (familiarity with the
// subject at hand) — a PhD can be new to a topic outside their field.
const EDUCATION_LEVELS = ["High school", "Undergraduate", "Master's", "Doctorate", "Professional"]
const NOTES_PURPOSES = [
  "Lectures",
  "Online courses",
  "Research",
  "Exam prep",
  "Self-study",
  "Work",
]
const EMPHASIZE = [
  "Definitions",
  "Examples",
  "Formulas & derivations",
  "Procedures & steps",
  "Reasoning & proofs",
  "Connections between topics",
  "Key takeaways",
  "Code snippets",
]

const EXTRA_MAX = 200

// Normalized form state, used to tell whether anything actually changed.
// The multi-selects are sorted because toggling appends, so picking the same
// options in a different order would otherwise look like an edit. Strings are
// trimmed to match what the server stores.
function snapshot(name: string, fields: ProfileFields): string {
  return JSON.stringify({
    name: name.trim(),
    occupation: fields.occupation.trim(),
    background_level: fields.background_level,
    education_level: fields.education_level,
    notes_purpose: [...fields.notes_purpose].sort(),
    emphasize: [...fields.emphasize].sort(),
    extra: fields.extra.trim(),
  })
}

type Props = {
  open: boolean
  onClose: () => void
  onSaved: (profile: UserProfileState) => void
}

// Shared pill styling for the single- and multi-select answers.
function Choice({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-lg border px-3 py-1.5 text-sm ${FOCUS_RING} ${
        active
          ? "border-transparent bg-primary text-primary-foreground"
          : "border-border hover:bg-[var(--hover)]"
      }`}
    >
      {label}
    </button>
  )
}

export function ProfileDialog({ open, onClose, onSaved }: Props) {
  const [name, setName] = useState("")
  const [fields, setFields] = useState<ProfileFields>(EMPTY_FIELDS)
  const [compiled, setCompiled] = useState("")
  const [isEdited, setIsEdited] = useState(false)
  const [compileFailed, setCompileFailed] = useState(false)
  // What the server last confirmed. Save stays disabled until the form
  // diverges from it.
  const [savedSnapshot, setSavedSnapshot] = useState(() => snapshot("", EMPTY_FIELDS))
  // Instructions are saved by Save now rather than on blur, so their
  // last-saved value is tracked the same way.
  const [savedCompiled, setSavedCompiled] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmRegenerate, setConfirmRegenerate] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [open, onClose])

  // Load fresh each time it opens, so the form reflects server state rather
  // than whatever was last typed and abandoned.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setError(null)
    api
      .getProfile()
      .then((p) => {
        if (cancelled) return
        setName(p.name)
        setFields({ ...EMPTY_FIELDS, ...p.fields })
        setCompiled(p.compiled_prompt ?? "")
        setIsEdited(p.is_edited)
        setCompileFailed(!p.compiled_prompt && Boolean(p.compile_failed_at))
        setSavedSnapshot(snapshot(p.name, { ...EMPTY_FIELDS, ...p.fields }))
        setSavedCompiled(p.compiled_prompt ?? "")
      })
      .catch(() => {
        // Non-fatal — an empty form still saves fine.
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const answersChanged = snapshot(name, fields) !== savedSnapshot
  const instructionsChanged = compiled.trim() !== savedCompiled.trim()
  const isDirty = answersChanged || instructionsChanged

  if (!open) return null

  function apply(saved: UserProfileState) {
    setCompiled(saved.compiled_prompt ?? "")
    setIsEdited(saved.is_edited)
    // A failed compile is reported inline, not as a form-level error: the
    // answers did save.
    setCompileFailed(!saved.compiled_prompt && Boolean(saved.compile_failed_at))
    setSavedSnapshot(snapshot(saved.name, { ...EMPTY_FIELDS, ...saved.fields }))
    setSavedCompiled(saved.compiled_prompt ?? "")
    onSaved(saved)
  }

  function setField<K extends keyof ProfileFields>(key: K, value: ProfileFields[K]) {
    setError(null)
    setFields((prev) => ({ ...prev, [key]: value }))
  }

  function toggle(key: "notes_purpose" | "emphasize", value: string) {
    const current = fields[key]
    setField(key, current.includes(value) ? current.filter((v) => v !== value) : [...current, value])
  }

  async function save(regenerate = false) {
    setBusy(true)
    setError(null)
    try {
      // Hand-edited description is saved FIRST. That sets is_edited
      // server-side, so the profile save below won't recompile over them —
      // no wasted LLM call, and the user's wording wins.
      if (instructionsChanged && !regenerate) {
        await api.saveCompiledPrompt(compiled)
      }
      // Compilation runs inside this request, so the response already
      // carries the new description — show them rather than closing.
      apply(await api.saveProfile({ name, fields, regenerate }))
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }

  // Recompiling would destroy a hand-written description, so ask first — but
  // only about wording from a PREVIOUS session. If the user just edited the
  // box in front of them, keeping it is obviously what they want.
  function handleSave() {
    if (isEdited && !instructionsChanged && answersChanged) {
      setConfirmRegenerate(true)
      return
    }
    void save()
  }

  async function handleRegenerate() {
    setBusy(true)
    setError(null)
    try {
      // Save the current answers first, otherwise Regenerate would compile
      // from whatever was last persisted rather than what's on screen.
      await api.saveProfile({ name, fields, regenerate: true })
      apply(await api.regenerateProfilePrompt())
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }

  async function handleClear() {
    setBusy(true)
    setError(null)
    try {
      await api.deleteProfile()
      setName("")
      setFields(EMPTY_FIELDS)
      setCompiled("")
      setIsEdited(false)
      setCompileFailed(false)
      setSavedSnapshot(snapshot("", EMPTY_FIELDS))
      setSavedCompiled("")
      onSaved({
        name: "",
        fields: EMPTY_FIELDS,
        compiled_prompt: null,
        is_edited: false,
        has_profile: false,
        compile_failed_at: null,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    // Fragment, not a wrapper: the confirmations below sit OUTSIDE the
    // overlay on purpose. React portals bubble events through the React
    // tree, so a confirm nested inside the overlay would have its backdrop
    // click reach onClose and close the profile dialog too.
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] px-6"
        onClick={onClose}
      >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-dialog-title"
        className="thin-scrollbar max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-background p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="profile-dialog-title" className="font-heading text-lg font-medium tracking-tight">
              Your profile
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className={`-m-1 shrink-0 rounded-full p-1 text-[var(--muted)] hover:bg-[var(--hover)] ${FOCUS_RING}`}
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Name</span>
              <input
                className={`${FIELD} ${FOCUS_RING}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Karl"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Occupation or field</span>
              <input
                className={`${FIELD} ${FOCUS_RING}`}
                value={fields.occupation}
                onChange={(e) => setField("occupation", e.target.value)}
                placeholder="Medical student, engineer, …"
              />
            </label>
          </div>

          <div>
            <span className="mb-1.5 block text-sm font-medium">Education level</span>
            <div className="flex flex-wrap gap-2">
              {EDUCATION_LEVELS.map((level) => (
                <Choice
                  key={level}
                  label={level}
                  active={fields.education_level === level}
                  onClick={() =>
                    setField("education_level", fields.education_level === level ? "" : level)
                  }
                />
              ))}
            </div>
          </div>

          <div>
            <span className="mb-1.5 block text-sm font-medium">Background level</span>
            <div className="flex flex-wrap gap-2">
              {BACKGROUND_LEVELS.map((level) => (
                <Choice
                  key={level}
                  label={level}
                  active={fields.background_level === level}
                  // Clicking the active one clears it — otherwise there's no
                  // way back to "unset" once an answer is picked.
                  onClick={() =>
                    setField("background_level", fields.background_level === level ? "" : level)
                  }
                />
              ))}
            </div>
          </div>

          <div>
            <span className="mb-1.5 block text-sm font-medium">What you're using notes for</span>
            <div className="flex flex-wrap gap-2">
              {NOTES_PURPOSES.map((purpose) => (
                <Choice
                  key={purpose}
                  label={purpose}
                  active={fields.notes_purpose.includes(purpose)}
                  onClick={() => toggle("notes_purpose", purpose)}
                />
              ))}
            </div>
          </div>

          <div>
            <span className="mb-1.5 block text-sm font-medium">Emphasize</span>
            <div className="flex flex-wrap gap-2">
              {EMPHASIZE.map((item) => (
                <Choice
                  key={item}
                  label={item}
                  active={fields.emphasize.includes(item)}
                  onClick={() => toggle("emphasize", item)}
                />
              ))}
            </div>
          </div>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">Anything else</span>
            <textarea
              className={`thin-scrollbar resize-none ${FIELD} ${FOCUS_RING}`}
              rows={2}
              maxLength={EXTRA_MAX}
              value={fields.extra}
              onChange={(e) => setField("extra", e.target.value)}
              placeholder="Exam in 6 weeks, prefer worked examples…"
            />
            <span className="mt-1 block text-right text-xs text-[var(--muted)]">
              {fields.extra.length}/{EXTRA_MAX}
            </span>
          </label>

          {!compiled && compileFailed ? (
            <div className="-mt-2 rounded-lg border border-border bg-[var(--sidebar)] px-3 py-2.5">
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 text-sm text-[var(--muted)]">
                  Your answers are saved, but the description couldn't be generated.
                </p>
                <button
                  type="button"
                  onClick={() => void handleRegenerate()}
                  disabled={busy}
                  className={`flex shrink-0 items-center gap-1 text-xs text-[var(--muted)] hover:text-foreground disabled:opacity-50 ${FOCUS_RING}`}
                >
                  <RefreshCw className={`size-3.5 ${busy ? "animate-spin" : ""}`} />
                  Retry
                </button>
              </div>
            </div>
          ) : null}

          {compiled ? (
            <div className="-mt-2 block">
              <span className="mb-1 block text-sm font-medium">
                About you
                {isEdited ? (
                  <span className="ml-1.5 text-xs font-normal text-[var(--muted)]">(edited by you)</span>
                ) : null}
              </span>
              <textarea
                className={`thin-scrollbar resize-none ${FIELD} ${FOCUS_RING}`}
                rows={6}
                value={compiled}
                onChange={(e) => setCompiled(e.target.value)}
                />
              <span className="mt-1 block text-xs text-[var(--muted)]">
                A short description of you, pasted into the AI as context. Edit if you want.
              </span>
            </div>
          ) : null}
        </div>

        {error ? (
          <div
            role="alert"
            className="mt-3 flex items-start gap-2.5 rounded-xl border border-[var(--error)]/25 bg-[var(--error-bg)] px-3.5 py-2.5 text-[var(--error)]"
          >
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <p className="min-w-0 flex-1 text-sm leading-snug break-words">{error}</p>
          </div>
        ) : null}

        <div className="mt-5 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setConfirmClear(true)}
            disabled={busy}
            className={`rounded text-sm text-[var(--muted)] hover:text-[var(--error)] disabled:opacity-50 ${FOCUS_RING}`}
          >
            Clear profile
          </button>
          <div className="flex gap-2">
            <Button type="button" onClick={() => void handleSave()} disabled={busy || !isDirty}>
              {busy ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Saving…
                </>
              ) : (
                "Save"
              )}
            </Button>
          </div>
        </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmRegenerate}
        title="Replace your edited description?"
        description="You rewrote the description by hand. Saving these answers will compile a new version and discard your wording."
        confirmLabel="Replace"
        cancelLabel="Keep mine"
        destructive
        onConfirm={() => {
          setConfirmRegenerate(false)
          void save(true)
        }}
        // "Keep mine" still saves the answers — it only declines the recompile.
        onCancel={() => {
          setConfirmRegenerate(false)
          void save(false)
        }}
      />

      <ConfirmDialog
        open={confirmClear}
        title="Clear your profile?"
        description="Your answers and the generated description will be deleted. Notes will be written without personalization until you fill it in again."
        confirmLabel="Clear"
        destructive
        onConfirm={() => {
          setConfirmClear(false)
          void handleClear()
        }}
        onCancel={() => setConfirmClear(false)}
      />
    </>,
    document.body
  )
}
