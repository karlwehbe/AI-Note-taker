// User turns render as a small right-aligned pill (just the transcript, no
// filename/mic tag — unless it came from a genuine file upload, which shows
// as a file chip with a "Show transcript" toggle instead of the transcript
// directly). Assistant turns are always Markdown (enforced by the system
// prompt server-side) and render as plain full-width prose — no bubble, no
// border — so notes read like content on the page rather than a chat
// message. Fresh assistant replies can optionally stream in (client-side
// reveal) so they feel like a live chat model response.
import { useState } from "react"
import { ChevronDown, ChevronUp, FileAudio } from "lucide-react"

import { Markdown, pendingMathItalicHtml, splitIncompleteMath } from "@/components/markdown"
import type { Message } from "@/lib/api"
import { useStreamReveal } from "@/lib/use-stream-reveal"

// Recordings store filename "recording.webm" as metadata (no audio upload)
// — only a genuine file upload should render as a file chip instead of its
// transcript text.
function isFileAttachment(message: Message) {
  return Boolean(message.filename && message.filename !== "recording.webm")
}

// The colored bubble — shared by a plain user message and by a file
// attachment's revealed transcript.
//
// The width cap is min(32rem, 100%) rather than a plain max-w-lg (32rem):
// these bubbles sit inside flex containers that size children by
// fit-content, whose floor is the content's *min-content* width. A fixed
// 32rem cap never clamps below that floor, so on a narrow screen (or with
// the notes panel open) the bubble stays min-content-wide and overflows.
// A percentage cap clamps against the actual available width instead.
const BUBBLE_MAX_W = "max-w-[min(32rem,100%)]"

function TranscriptBubble({ content }: { content: string }) {
  return (
    <div
      className={`min-w-0 ${BUBBLE_MAX_W} rounded-2xl bg-primary px-4 py-2.5 text-base break-words whitespace-pre-wrap text-primary-foreground`}
    >
      {content}
    </div>
  )
}

function FileAttachmentBubble({ message }: { message: Message }) {
  const [showTranscript, setShowTranscript] = useState(false)
  // Pending uploads have a filename but no transcript yet (server still
  // transcribing). Don't offer "Show transcript" until there's something to
  // show — expanding an empty bubble looked broken.
  const hasTranscript = Boolean(message.content.trim())
  return (
    // w-full (not shrink-to-fit) so the percentage caps on the children
    // below resolve against the row's real width; items-end keeps them
    // right-aligned as before.
    <div className="flex w-full min-w-0 flex-col items-end gap-1.5">
      {hasTranscript ? (
        <button
          type="button"
          onClick={() => setShowTranscript((s) => !s)}
          aria-label={showTranscript ? "Hide transcript" : "Show transcript"}
          aria-expanded={showTranscript}
          className={`inline-flex min-w-0 ${BUBBLE_MAX_W} items-center gap-1.5 rounded-2xl border border-border bg-[var(--sidebar)] py-1 pr-2 pl-2.5 text-sm text-foreground hover:bg-[var(--hover)]`}
        >
          <FileAudio className="size-3.5 shrink-0 text-[var(--muted)]" />
          {/* min-w-0 is what lets truncate actually clip — without it this
              span's nowrap min-content width props the whole chip open. */}
          <span className="min-w-0 truncate">{message.filename}</span>
          {showTranscript ? (
            <ChevronUp className="size-3.5 shrink-0 text-[var(--muted)]" />
          ) : (
            <ChevronDown className="size-3.5 shrink-0 text-[var(--muted)]" />
          )}
        </button>
      ) : (
        <div
          className={`inline-flex min-w-0 ${BUBBLE_MAX_W} items-center gap-1.5 rounded-2xl border border-border bg-[var(--sidebar)] py-1 pr-2.5 pl-2.5 text-sm text-foreground`}
        >
          <FileAudio className="size-3.5 shrink-0 text-[var(--muted)]" />
          <span className="min-w-0 truncate">{message.filename}</span>
        </div>
      )}
      {hasTranscript && showTranscript ? <TranscriptBubble content={message.content} /> : null}
    </div>
  )
}

export function MessageBubble({
  message,
  stream = false,
  onStreamTick,
  onStreamDone,
}: {
  message: Message
  /** Client-side token reveal for a freshly arrived assistant reply. */
  stream?: boolean
  onStreamTick?: () => void
  onStreamDone?: () => void
}) {
  const streaming = stream && message.role === "assistant"
  const { text: revealed, done } = useStreamReveal(
    message.content,
    streaming,
    onStreamTick,
    onStreamDone,
  )

  if (message.role === "user") {
    return (
      <div className="flex min-w-0 justify-end">
        {isFileAttachment(message) ? (
          <FileAttachmentBubble message={message} />
        ) : (
          <TranscriptBubble content={message.content} />
        )}
      </div>
    )
  }

  // break-words + scrollable code blocks: assistant Markdown can contain a
  // long URL or code line that would otherwise push the column wide.
  // Incomplete math streams as italic plain TeX in the same markdown flow,
  // then swaps to KaTeX when the closer arrives — never feed an unclosed $
  // to remark-math.
  const source =
    streaming && !done
      ? (() => {
          const { complete, pending } = splitIncompleteMath(revealed)
          return pending ? complete + pendingMathItalicHtml(pending) : complete
        })()
      : revealed

  return (
    <div className="prose prose-base w-full min-w-0 max-w-none font-sans break-words [&_pre]:overflow-x-auto">
      <Markdown>{source}</Markdown>
      {streaming && !done ? <span className="stream-caret" aria-hidden /> : null}
    </div>
  )
}
