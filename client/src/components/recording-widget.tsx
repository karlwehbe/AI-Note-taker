// Floating "recording in progress elsewhere" indicator — shown at the
// bottom-left, above everything, whenever a recording is active but the
// user has navigated away from the conversation it belongs to. No send
// button here on purpose: sending only ever happens from the conversation's
// own composer, once the user comes back to it. Clicking the widget (outside
// the pause button) jumps straight back to that conversation.
import { createPortal } from "react-dom"
import { useNavigate } from "@tanstack/react-router"
import { Pause, Play } from "lucide-react"

import { useAudioLevel, useRecordingContext } from "@/lib/recording-context"

// Per-bar weighting so the waveform doesn't pulse as one flat block — each
// bar reacts to the same audio level a little differently.
const BAR_WEIGHTS = [0.45, 0.8, 1, 0.65, 0.35]
const BAR_MAX_HEIGHT = 24

function formatElapsed(totalSeconds: number) {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

export function RecordingWidget() {
  const recording = useRecordingContext()
  const audioLevel = useAudioLevel()
  const navigate = useNavigate()

  if (!recording.isRecording || recording.isHostViewingRecording) return null

  const targetId = recording.recordingConversationId

  return createPortal(
    <div
      role="status"
      aria-label="Recording in progress"
      onClick={() => {
        if (targetId) void navigate({ to: "/c/$conversationId", params: { conversationId: targetId } })
      }}
      className={`fixed bottom-6 left-6 z-[100] flex items-center gap-3 rounded-2xl border border-border bg-background px-4 py-3 shadow-lg ${
        targetId ? "cursor-pointer" : ""
      }`}
    >
      <span className="relative flex size-2 shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--error)] opacity-75" />
        <span className="relative inline-flex size-2 rounded-full bg-[var(--error)]" />
      </span>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          recording.togglePause()
        }}
        className="shrink-0 rounded-full p-2 text-[var(--muted)] hover:bg-[var(--hover)]"
        aria-label={recording.isPaused ? "Resume recording" : "Pause recording"}
        title={recording.isPaused ? "Resume" : "Pause"}
      >
        {recording.isPaused ? <Play className="size-4" /> : <Pause className="size-4" />}
      </button>

      <div className="flex h-6 items-end gap-0.5" aria-hidden="true">
        {BAR_WEIGHTS.map((weight, i) => {
          const level = recording.isPaused ? 0.1 : Math.max(0.12, audioLevel * weight)
          return (
            <span
              key={i}
              className="w-1 rounded-full bg-[var(--accent)] transition-all duration-100 ease-out"
              style={{ height: `${Math.round(level * BAR_MAX_HEIGHT)}px` }}
            />
          )
        })}
      </div>

      <span className="min-w-10 text-sm text-[var(--muted)] tabular-nums">
        {formatElapsed(recording.elapsedSeconds)}
      </span>
    </div>,
    document.body
  )
}
