// Renders a ```mermaid fenced block as an actual diagram.
//
// Mermaid is imported dynamically rather than at module load: it's by far the
// largest dependency in the app, and most notes contain no diagrams at all,
// so pulling it into the main bundle would slow every page load to serve a
// minority of documents.
//
// Diagram source here is model-generated, so it is frequently malformed —
// mermaid throws on a syntax error. Every failure falls back to showing the
// source as a normal code block. A broken diagram must never blank a set of
// notes the user just recorded an hour of lecture for.
import { useEffect, useId, useState } from "react"

function prefersDark(): boolean {
  if (typeof document === "undefined") return false
  return document.documentElement.classList.contains("dark")
}

// Survives ReactMarkdown remounting this component when notes update but
// the diagram text is unchanged — without this, every send flashes the
// loading pulse even for diagrams that didn't change.
const svgCache = new Map<string, string>()

function cacheKey(chart: string): string {
  return `${prefersDark() ? "dark" : "light"}:${chart}`
}

export function MermaidDiagram({ chart }: { chart: string }) {
  const key = cacheKey(chart)
  const [svg, setSvg] = useState<string | null>(() => svgCache.get(key) ?? null)
  const [failed, setFailed] = useState(false)
  // Mermaid needs a DOM id per render call, unique across all diagrams in
  // the document. useId gives one without mutating anything during render;
  // the colons it contains are stripped because mermaid puts this straight
  // into an id attribute and CSS selector.
  const domId = `mermaid-${useId().replace(/:/g, "")}`

  useEffect(() => {
    const cached = svgCache.get(key)
    if (cached !== undefined) {
      setSvg(cached)
      setFailed(false)
      return
    }

    let cancelled = false
    setFailed(false)
    setSvg(null)

    async function render() {
      try {
        const mermaid = (await import("mermaid")).default
        mermaid.initialize({
          startOnLoad: false,
          // Labels in a diagram come from the same model output as the rest
          // of the notes, so they get the strict treatment: mermaid escapes
          // HTML in labels rather than injecting it.
          securityLevel: "strict",
          theme: prefersDark() ? "dark" : "default",
          fontFamily: "inherit",
        })
        // parse() first so a syntax error is caught without mermaid leaving
        // an orphaned error node attached to the document body.
        await mermaid.parse(chart)
        const { svg: rendered } = await mermaid.render(domId, chart)
        if (!cancelled) {
          svgCache.set(key, rendered)
          setSvg(rendered)
        }
      } catch {
        if (!cancelled) setFailed(true)
      }
    }
    void render()

    return () => {
      cancelled = true
    }
  }, [chart, key, domId])

  if (failed) {
    // Same shape a normal fenced block would have taken, so a diagram the
    // model got wrong degrades to exactly what it would have rendered as
    // without mermaid support at all.
    return (
      <pre>
        <code>{chart}</code>
      </pre>
    )
  }

  if (svg === null) {
    // Reserve a little height so the surrounding prose doesn't jump when the
    // diagram resolves.
    return <div className="my-4 h-24 animate-pulse rounded-xl bg-[var(--hover)]" />
  }

  return (
    <div
      // Wide diagrams scroll inside their own container rather than forcing
      // the notes panel to scroll horizontally.
      className="thin-scrollbar my-4 overflow-x-auto rounded-xl border border-border bg-background p-3 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-none"
      // Safe: this is mermaid's own SVG output, generated from parsed diagram
      // source with securityLevel "strict" — not raw model text.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
