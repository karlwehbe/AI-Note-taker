// Shown in the message list while waiting for the AI's response — replaces
// the composer's send button carrying a loading state, since the wait is
// really about the chat, not the button itself.
export function GeneratingIndicator() {
  return (
    <div className="flex items-center gap-1.5 text-base text-[var(--muted)]">
      <span>Generating</span>
      <span className="flex items-end gap-0.5 pb-0.5">
        <span
          className="typing-dot size-1 rounded-full bg-[var(--muted)]"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="typing-dot size-1 rounded-full bg-[var(--muted)]"
          style={{ animationDelay: "200ms" }}
        />
        <span
          className="typing-dot size-1 rounded-full bg-[var(--muted)]"
          style={{ animationDelay: "400ms" }}
        />
      </span>
    </div>
  )
}
