// Left sidebar: brand mark, "New chat", and the conversation list — the
// persistent chrome around every route (new-chat empty state and individual
// conversation threads alike). Collapsible — collapsed state persists across
// reloads via localStorage. When collapsed, shrinks to a slim rail with just
// an expand button and an icon-only "new chat" shortcut.
import { useEffect, useState } from "react"
import { Link, useNavigate, useParams } from "@tanstack/react-router"
import { PanelLeftClose, PanelLeftOpen, Plus, User, X } from "lucide-react"

import { ConfirmDialog } from "@/components/confirm-dialog"
import { ProfileDialog } from "@/components/profile-dialog"
import { api } from "@/lib/api"
import { useConversationsContext } from "@/lib/conversations-context"
import { useRecordingContext } from "@/lib/recording-context"
import type { Conversation, UserProfileState } from "@/lib/api"

const COLLAPSED_KEY = "sidebar-collapsed"

// Applied to every hand-rolled interactive element below instead of relying
// on the browser's default focus outline, which on some browsers/OSes
// renders as a yellow/gold ring that reads oddly against the white UI.
const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"

// One row shape shared by the action items ("New chat") and the conversation
// list, so the whole sidebar reads as a single list rather than a bordered
// button sitting above a list of plain rows. The hover is kept separate
// because a conversation row that's currently active swaps it for a static
// background instead (see below).
const SIDEBAR_ROW_BASE = "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm"
const SIDEBAR_ROW = `${SIDEBAR_ROW_BASE} hover:bg-[var(--sidebar-accent)]`

export function Sidebar() {
  const { conversations, loading, refetch } = useConversationsContext()
  const recording = useRecordingContext()
  const navigate = useNavigate()
  const params = useParams({ strict: false })
  const activeId = params.conversationId

  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && localStorage.getItem(COLLAPSED_KEY) === "true"
  )
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null)
  const [showProfile, setShowProfile] = useState(false)
  const [profile, setProfile] = useState<UserProfileState | null>(null)
  // Separate from `profile === null`, which can't tell "still loading" from
  // "loaded, but there's no profile yet". Without it the row renders "Set up
  // your profile" during the fetch and then swaps to the real name.
  const [profileLoading, setProfileLoading] = useState(true)

  // Loaded once for the sidebar row's label; the dialog refetches its own
  // copy when opened and hands the saved result back via onSaved.
  useEffect(() => {
    let cancelled = false
    api
      .getProfile()
      .then((p) => {
        if (!cancelled) setProfile(p)
      })
      .catch(() => {
        // Non-fatal — the row just falls back to "Set up your profile".
      })
      .finally(() => {
        if (!cancelled) setProfileLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])
  const pendingDeleteIsRecording =
    pendingDelete !== null && recording.isRecording && recording.recordingConversationId === pendingDelete.id

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev
      localStorage.setItem(COLLAPSED_KEY, String(next))
      return next
    })
  }

  function requestDelete(e: React.MouseEvent, conversation: Conversation) {
    e.preventDefault()
    e.stopPropagation()
    setPendingDelete(conversation)
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    const id = pendingDelete.id
    setPendingDelete(null)
    // Deleting the conversation a recording belongs to (inline or currently
    // backgrounded behind the floating widget) would otherwise orphan it —
    // its autosaves start silently 404ing, and the widget would still point
    // at a conversation that's gone. Stop it first.
    if (recording.isRecording && recording.recordingConversationId === id) {
      recording.discardRecording()
    }
    try {
      await api.deleteConversation(id)
    } catch {
      // Already gone — discardRecording above may have just deleted it
      // itself (if it was a conversation created for that recording and
      // never sent). Either way the end state is the same.
    }
    await refetch()
    if (activeId === id) {
      void navigate({ to: "/" })
    }
  }

  if (collapsed) {
    return (
      <aside className="flex h-svh w-14 shrink-0 flex-col items-center gap-2 border-r border-border bg-[var(--sidebar)] py-4">
        <button
          type="button"
          onClick={toggleCollapsed}
          className={`rounded-md p-2 text-[var(--muted)] hover:bg-[var(--hover)] ${FOCUS_RING}`}
          aria-label="Expand sidebar"
          title="Expand sidebar"
        >
          <PanelLeftOpen className="size-5" />
        </button>
        <Link
          to="/"
          className={`rounded-md p-2 text-[var(--muted)] hover:bg-[var(--hover)] ${FOCUS_RING}`}
          aria-label="New chat"
          title="New chat"
        >
          <Plus className="size-5" />
        </Link>
        <button
          type="button"
          onClick={() => setShowProfile(true)}
          className={`mt-auto rounded-md p-2 text-[var(--muted)] hover:bg-[var(--hover)] ${FOCUS_RING}`}
          aria-label="Your profile"
          title={profile?.name?.trim() || "Your profile"}
        >
          <User className="size-5" />
        </button>
        <ProfileDialog open={showProfile} onClose={() => setShowProfile(false)} onSaved={setProfile} />
      </aside>
    )
  }

  const showChatHeading = loading || conversations.length > 0

  return (
    <aside className="flex h-svh w-64 shrink-0 flex-col border-r border-border bg-[var(--sidebar)]">
      <div className="flex items-center justify-between p-4">
        <Link
          to="/"
          className={`rounded font-heading text-base font-medium tracking-tight ${FOCUS_RING}`}
        >
          AI Note Taker
        </Link>
        <button
          type="button"
          onClick={toggleCollapsed}
          className={`rounded-md p-1.5 text-[var(--muted)] hover:bg-[var(--hover)] ${FOCUS_RING}`}
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
        >
          <PanelLeftClose className="size-5" />
        </button>
      </div>

      <div className="space-y-0.5 px-3">
        <Link to="/" className={`${SIDEBAR_ROW} ${FOCUS_RING}`}>
          <Plus className="size-4 shrink-0 text-[var(--muted)]" />
          New chat
        </Link>
      </div>

      {showChatHeading ? (
        <div className="mt-4 px-3">
          <h2 className="px-2.5 pb-1 text-xs font-medium tracking-wide text-[var(--muted)] uppercase">
            Chats
          </h2>
        </div>
      ) : null}

      <nav
        className="thin-scrollbar flex-1 space-y-1 overflow-y-auto px-3"
        aria-busy={loading}
        aria-label="Conversations"
      >
        {loading ? (
          <ConversationListSkeleton />
        ) : (
          conversations.map((c) => (
            <Link
              key={c.id}
              to="/c/$conversationId"
              params={{ conversationId: c.id }}
              // Own token (--sidebar-accent), deliberately separate from the
              // generic --hover used elsewhere (e.g. the mic/computer-audio
              // menu) — computed manually rather than via activeProps layered
              // on a separate static hover class, since those would point at
              // two different colors and race in the compiled CSS for whichever
              // wins on :hover while a row is active.
              className={`group justify-between ${SIDEBAR_ROW_BASE} ${FOCUS_RING} ${
                c.id === activeId ? "bg-[var(--sidebar-accent)] font-medium" : "hover:bg-[var(--sidebar-accent)]"
              }`}
            >
              <span className="truncate">{c.title}</span>
              <button
                type="button"
                onClick={(e) => requestDelete(e, c)}
                className={`shrink-0 rounded p-1 text-[var(--muted)] opacity-0 hover:text-[var(--error)] group-hover:opacity-100 ${FOCUS_RING}`}
                aria-label={`Delete "${c.title}"`}
              >
                <X className="size-4" />
              </button>
            </Link>
          ))
        )}
      </nav>

      <div className="border-t border-border p-2" aria-busy={profileLoading}>
        {profileLoading ? (
          <ProfileRowSkeleton />
        ) : (
          <button
            type="button"
            onClick={() => setShowProfile(true)}
            className={`${SIDEBAR_ROW} ${FOCUS_RING}`}
            title="Tell the AI who it's writing for"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--sidebar-accent)] text-[var(--muted)]">
              <User className="size-4" />
            </span>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate">
                {profile?.name?.trim() || (profile?.has_profile ? "Your profile" : "Set up your profile")}
              </span>
              {profile?.fields?.occupation?.trim() ? (
                <span className="block truncate text-xs text-[var(--muted)]">
                  {profile.fields.occupation}
                </span>
              ) : null}
            </span>
          </button>
        )}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete conversation?"
        description={
          pendingDelete
            ? `"${pendingDelete.title}" and its notes will be permanently deleted. This can't be undone.${
                pendingDeleteIsRecording ? " It also has a recording in progress — that will be stopped too." : ""
              }`
            : ""
        }
        confirmLabel="Delete"
        destructive
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />

      <ProfileDialog open={showProfile} onClose={() => setShowProfile(false)} onSaved={setProfile} />
    </aside>
  )
}

/** Placeholder rows matching conversation link height while the list loads. */
function ConversationListSkeleton() {
  return (
    <div className="space-y-1" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        // Same row chrome + delete-button footprint as a real chat link so
        // the bars line up with where truncated titles sit.
        <div key={i} className={`${SIDEBAR_ROW_BASE} pointer-events-none justify-between`}>
          <div
            className="h-5 min-w-0 flex-1 animate-pulse rounded bg-[var(--sidebar-accent)]"
            style={{ animationDelay: `${i * 90}ms` }}
          />
          <div className="size-6 shrink-0" />
        </div>
      ))}
    </div>
  )
}

/** Placeholder matching the profile row while the profile loads. */
function ProfileRowSkeleton() {
  return (
    // Same row chrome and the same size-7 avatar circle as the real button,
    // so the label lands exactly where the bar was. Two bars because the
    // filled-in profile — name over occupation — is the steady state; a
    // profile with no occupation renders one line and settles 8px shorter.
    <div className={`${SIDEBAR_ROW_BASE} pointer-events-none`} aria-hidden>
      <span className="size-7 shrink-0 animate-pulse rounded-full bg-[var(--sidebar-accent)]" />
      <span className="min-w-0 flex-1 space-y-1">
        {/* h-4/h-3 rather than the text's own line-height: bars sized to the
            glyphs read as text, where full-line-height bars read as blocks. */}
        <span
          className="block h-4 w-24 animate-pulse rounded bg-[var(--sidebar-accent)]"
          // Offset like the conversation rows above, so the sidebar pulses as
          // one thing rather than several independent loaders.
          style={{ animationDelay: "180ms" }}
        />
        <span
          className="block h-3 w-16 animate-pulse rounded bg-[var(--sidebar-accent)]"
          style={{ animationDelay: "270ms" }}
        />
      </span>
    </div>
  )
}
