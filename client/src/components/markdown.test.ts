// Unit tests for the client-side math normalisation pipeline.
//
// This is the mirror of the server's _repair_latex_escapes, and it has the
// same failure mode: every bug here shows up as visibly wrong maths in the
// notes panel, and the only way anyone has noticed so far is by reading the
// rendered page.
import { describe, expect, it } from "vitest"

import {
  clipIncompleteMath,
  pendingMathPlain,
  pendingMathItalicHtml,
  collapseOverEscapedCommands,
  isDisplayWorthy,
  normalizeMath,
  repairMangledDollars,
  restoreMissingBackslash,
  splitIncompleteMath,
} from "./markdown"

// Built rather than written as a literal so no tool can quietly turn the
// six characters $ into the "$" they denote.
const ESC = String.fromCharCode(92) + "u0024"

describe("repairMangledDollars", () => {
  it("turns the unicode escape into a dollar", () => {
    expect(repairMangledDollars(`${ESC}x${ESC}`)).toBe("$x$")
  })

  it("does not double up when the escape sits inside real delimiters", () => {
    expect(repairMangledDollars(`$${ESC}x${ESC}$`)).toBe("$x$")
  })

  it("leaves real display delimiters alone", () => {
    expect(repairMangledDollars("$$x$$")).toBe("$$x$$")
  })

  it("leaves prose alone", () => {
    const text = "A vector has magnitude and direction."
    expect(repairMangledDollars(text)).toBe(text)
  })
})

describe("restoreMissingBackslash", () => {
  it("restores a dropped backslash on a known command", () => {
    expect(restoreMissingBackslash("hat{i}")).toBe(String.raw`\hat{i}`)
  })

  it("replaces a control character before a command", () => {
    expect(restoreMissingBackslash("\x05hat{i}")).toBe(String.raw`\hat{i}`)
  })

  it("does not double an existing backslash", () => {
    expect(restoreMissingBackslash(String.raw`\hat{i}`)).toBe(String.raw`\hat{i}`)
  })
})

describe("collapseOverEscapedCommands", () => {
  it("collapses a doubled backslash on a command", () => {
    expect(collapseOverEscapedCommands(String.raw`\\frac{a}{b}`)).toBe(String.raw`\frac{a}{b}`)
  })

  it("leaves a matrix with numeric rows intact", () => {
    const source = String.raw`\begin{bmatrix}2&1\\0&3\end{bmatrix}`
    expect(collapseOverEscapedCommands(source)).toBe(source)
  })

  // Regression cover. `\\` is a row separator; the rule used to check only
  // that a letter followed, so a row beginning with one had its separator
  // eaten and became `\c`. Numeric rows survived, which is why numeric
  // examples rendered and symbolic ones did not.
  it("keeps row separators when the next row starts with a letter", () => {
    const source = String.raw`\begin{bmatrix}a&b\\c&d\end{bmatrix}`
    expect(collapseOverEscapedCommands(source)).toBe(source)
  })

  it("keeps row separators in an aligned environment", () => {
    const source = String.raw`\begin{aligned}x &= 1\\y &= 2\end{aligned}`
    expect(collapseOverEscapedCommands(source)).toBe(source)
  })

  it("keeps row separators in the exact reply that was broken", () => {
    const source = String.raw`\begin{bmatrix}a&b\\c&d\end{bmatrix}\begin{bmatrix}x\\y\end{bmatrix}`
    expect(collapseOverEscapedCommands(source)).toBe(source)
  })

  it("still collapses an over-escaped environment", () => {
    expect(collapseOverEscapedCommands(String.raw`\\begin{bmatrix}1\end{bmatrix}`)).toBe(
      String.raw`\begin{bmatrix}1\end{bmatrix}`,
    )
  })

  it("still collapses three backslashes before a command", () => {
    expect(collapseOverEscapedCommands(String.raw`\\\hat{i}`)).toBe(String.raw`\hat{i}`)
  })
})

describe("isDisplayWorthy", () => {
  it("treats an equation as display", () => {
    expect(isDisplayWorthy("x = y + 1")).toBe(true)
  })

  it("keeps a lone unit vector inline", () => {
    // Promoting $\hat{i}$ to its own centred block mid-sentence looks broken.
    expect(isDisplayWorthy(String.raw`\hat{i}`)).toBe(false)
  })

  it("treats an environment as display", () => {
    expect(isDisplayWorthy(String.raw`\begin{bmatrix}1\end{bmatrix}`)).toBe(true)
  })
})

describe("normalizeMath", () => {
  it("converts LaTeX-native inline delimiters", () => {
    // Models emit \( \) constantly; in Markdown a backslash before a paren is
    // just an escape, so these render as literal parens around raw TeX.
    expect(normalizeMath(String.raw`text \(x\) more`)).toContain("$x$")
  })

  it("converts LaTeX-native display delimiters", () => {
    expect(normalizeMath(String.raw`\[x = y + 1\]`)).toContain("$$")
  })

  it("leaves delimiters inside fenced code untouched", () => {
    // A code sample demonstrating LaTeX must not be turned into maths.
    const source = "```\n\\(x\\)\n```"
    expect(normalizeMath(source)).toBe(source)
  })

  it("leaves delimiters inside inline code untouched", () => {
    const source = "use `\\(x\\)` for inline maths"
    expect(normalizeMath(source)).toBe(source)
  })

  it("is idempotent", () => {
    // Notes are re-rendered on every keystroke of streamed text.
    const once = normalizeMath(String.raw`\(\hat{i}\) and $x = 1$`)
    expect(normalizeMath(once)).toBe(once)
  })

  it("leaves prose completely alone", () => {
    const text = "A matrix maps vectors to vectors."
    expect(normalizeMath(text)).toBe(text)
  })
})

describe("clipIncompleteMath", () => {
  it("leaves complete inline math alone", () => {
    expect(clipIncompleteMath("see $x$ now")).toBe("see $x$ now")
  })

  it("drops a trailing unclosed inline math span", () => {
    expect(clipIncompleteMath("see $x = ")).toBe("see ")
  })

  it("drops a trailing unclosed display math span", () => {
    const open = "intro $$\n\\frac{a}{b}"
    expect(clipIncompleteMath(open)).toBe("intro ")
  })

  it("keeps a finished display block and drops a later unclosed one", () => {
    expect(clipIncompleteMath("$$a$$ then $$b")).toBe("$$a$$ then ")
  })

  it("does not treat dollars inside code as math", () => {
    expect(clipIncompleteMath("use `$x` later")).toBe("use `$x` later")
  })
})

describe("splitIncompleteMath", () => {
  it("returns pending TeX for an unclosed display block", () => {
    expect(splitIncompleteMath("intro $$\n\\frac{a}{b}")).toEqual({
      complete: "intro ",
      pending: "$$\n\\frac{a}{b}",
    })
  })

  it("has an empty pending when math is closed", () => {
    expect(splitIncompleteMath("see $x$ now")).toEqual({
      complete: "see $x$ now",
      pending: "",
    })
  })
})

describe("pendingMathPlain", () => {
  it("strips display delimiters and collapses newlines to one line", () => {
    expect(pendingMathPlain("$$\n\\frac{a}{b}")).toBe("\\frac{a}{b}")
  })

  it("strips an inline dollar", () => {
    expect(pendingMathPlain("$x = ")).toBe("x = ")
  })
})

describe("pendingMathItalicHtml", () => {
  it("wraps escaped TeX in em.pending-tex", () => {
    expect(pendingMathItalicHtml("$a < b$".slice(0, -1))).toBe(
      '<em class="pending-tex">a &lt; b</em>',
    )
  })
})
