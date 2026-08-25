// Right-side panel showing the conversation's persistent lecture-notes
// document — similar to how Claude opens a document/artifact panel next to
// the chat. Only rendered once notes actually exist for the conversation.
// Width is user-resizable (drag the left edge) and collapsible — both
// persist across reloads, same pattern as the left Sidebar.
import { useEffect, useRef, useState } from "react"
import { PanelRightClose, PanelRightOpen } from "lucide-react"

import { Markdown } from "@/components/markdown"

const WIDTH_KEY = "notes-panel-width"
const COLLAPSED_KEY = "notes-panel-collapsed"
const MIN_WIDTH = 320
const MAX_WIDTH = 720
const DEFAULT_WIDTH = 420
const COLLAPSED_WIDTH = 48
/** First-open slide; keep under 1s. */
const ENTER_MS = 450

function clampWidth(width: number) {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width))
}

export function NotesPanel({
  content,
  animateEnter = false,
}: {
  content: string
  /** Slide open from width 0 when notes first appear (new chat / first create). */
  animateEnter?: boolean
}) {
  const [width, setWidth] = useState(() => {
    if (typeof window === "undefined") return DEFAULT_WIDTH
    const stored = Number(localStorage.getItem(WIDTH_KEY))
    return Number.isFinite(stored) && stored > 0 ? clampWidth(stored) : DEFAULT_WIDTH
  })
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== "undefined" && localStorage.getItem(COLLAPSED_KEY) === "true"
  )
  // Capture enter intent only on mount so a later prop flip doesn't cancel
  // the open animation mid-flight.
  const shouldAnimateRef = useRef(animateEnter)
  const [entered, setEntered] = useState(!shouldAnimateRef.current)
  const [isDragging, setIsDragging] = useState(false)
  const widthRef = useRef(width)
  const draggingRef = useRef(false)

  useEffect(() => {
    widthRef.current = width
  }, [width])

  useEffect(() => {
    if (!shouldAnimateRef.current) return
    // Paint width:0 first, then open — otherwise the transition never runs.
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setEntered(true))
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [])

  // Registered once (not per-drag) so we're not adding/removing listeners
  // on every pointermove during a drag — refs carry the live values instead.
  useEffect(() => {
    function onPointerMove(e: PointerEvent) {
      if (!draggingRef.current) return
      // Panel sits on the right edge; the handle is its left edge, so
      // dragging the pointer left (smaller clientX) should widen it.
      setWidth(clampWidth(window.innerWidth - e.clientX))
    }
    function onPointerUp() {
      if (!draggingRef.current) return
      draggingRef.current = false
      setIsDragging(false)
      localStorage.setItem(WIDTH_KEY, String(widthRef.current))
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
    }
    window.addEventListener("pointermove", onPointerMove)
    window.addEventListener("pointerup", onPointerUp)
    return () => {
      window.removeEventListener("pointermove", onPointerMove)
      window.removeEventListener("pointerup", onPointerUp)
    }
  }, [])

  function startDragging() {
    draggingRef.current = true
    setIsDragging(true)
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
  }

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev
      localStorage.setItem(COLLAPSED_KEY, String(next))
      return next
    })
  }

  const targetWidth = collapsed ? COLLAPSED_WIDTH : width
  const shownWidth = entered ? targetWidth : 0

  return (
    <aside
      // Named because the sidebar is also a <aside>: without this, assistive
      // tech (and any role-based query) sees two indistinguishable
      // complementary landmarks.
      aria-label="Notes"
      className="relative h-full shrink-0 overflow-hidden border-l border-border bg-[var(--sidebar)]"
      style={{
        width: shownWidth,
        opacity: entered ? 1 : 0,
        transition: isDragging
          ? "none"
          : `width ${ENTER_MS}ms ease-out, opacity ${ENTER_MS}ms ease-out`,
      }}
    >
      {/* Fixed inner width so content doesn't reflow while the panel slides open. */}
      <div className="flex h-full flex-col" style={{ width: targetWidth }}>
        {collapsed ? (
          <div className="flex h-full w-full flex-col items-center py-4">
            <button
              type="button"
              onClick={toggleCollapsed}
              className="rounded-md p-2 text-[var(--muted)] hover:bg-[var(--hover)]"
              aria-label="Expand notes"
              title="Expand notes"
            >
              <PanelRightOpen className="size-5" />
            </button>
          </div>
        ) : (
          <>
            <div
              onPointerDown={startDragging}
              className="absolute top-0 left-0 z-10 h-full w-1 cursor-col-resize hover:bg-border"
            />
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
              <h2 className="font-heading text-base font-medium tracking-tight">Notes</h2>
              <button
                type="button"
                onClick={toggleCollapsed}
                className="rounded-md p-1.5 text-[var(--muted)] hover:bg-[var(--hover)]"
                aria-label="Collapse notes"
                title="Collapse notes"
              >
                <PanelRightClose className="size-5" />
              </button>
            </div>
            <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto">
              <div className="prose prose-base max-w-none min-w-0 overflow-x-hidden px-6 py-6 font-sans break-words">
                <Markdown>{content}</Markdown>
              </div>
            </div>
          </>
        )}
      </div>
    </aside>
  )
}
