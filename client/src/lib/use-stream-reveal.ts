// Progressive reveal for assistant replies — the API returns the full
// message at once, so we fake the token stream chat models show. Cap the
// total duration so long replies don't take forever after the user already
// waited for generation.
import { useEffect, useState } from "react"

const TICK_MS = 48
/** Soft cap so a long reply finishes revealing in about this long. */
const MAX_DURATION_MS = 4000
/** Pace for typical replies — ~35 chars/sec feels closer to a live model. */
const TARGET_CHARS_PER_SEC = 35
const MIN_CHARS_PER_TICK = 1

function charsPerTick(length: number) {
  const idealTicks = Math.max(
    1,
    Math.ceil((length / TARGET_CHARS_PER_SEC) * (1000 / TICK_MS)),
  )
  const maxTicks = Math.max(1, Math.floor(MAX_DURATION_MS / TICK_MS))
  const ticks = Math.min(idealTicks, maxTicks)
  return Math.max(MIN_CHARS_PER_TICK, Math.ceil(length / ticks))
}

/** Advance to a word boundary when possible so mid-word cuts are rare. */
function nextIndex(text: string, from: number, step: number): number {
  if (from >= text.length) return text.length
  let target = Math.min(text.length, from + step)
  if (target >= text.length) return text.length
  // If we're mid-word, keep going to the next whitespace (small look-ahead).
  if (/\S/.test(text[target]!) && /\S/.test(text[target - 1]!)) {
    const ahead = text.indexOf(" ", target)
    const limit = Math.min(text.length, target + 16)
    if (ahead !== -1 && ahead < limit) target = ahead + 1
  }
  return target
}

export function useStreamReveal(
  text: string,
  active: boolean,
  onTick?: () => void,
  onDone?: () => void,
) {
  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches

  const [count, setCount] = useState(() => (active && !reducedMotion ? 0 : text.length))

  useEffect(() => {
    if (!active || reducedMotion) {
      setCount(text.length)
      if (active && reducedMotion) onDone?.()
      return
    }

    setCount(0)
    const step = charsPerTick(text.length)
    let i = 0
    const id = window.setInterval(() => {
      i = nextIndex(text, i, step)
      setCount(i)
      onTick?.()
      if (i >= text.length) {
        window.clearInterval(id)
        onDone?.()
      }
    }, TICK_MS)

    return () => window.clearInterval(id)
    // Intentionally only re-run when the streamed message identity/active flag
    // changes — onTick/onDone are event taps, not reactive deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, active, reducedMotion])

  return {
    text: text.slice(0, count),
    done: count >= text.length,
  }
}
