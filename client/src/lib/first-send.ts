// Bridges a first send that starts on "/" into the /c/$id page it navigates
// to mid-flight. ChatComposer creates the conversation, kicks off the AI
// request, registers it here, and navigates — so the title bar can show
// "New conversation" while generation runs. ConversationThread picks the
// entry up on mount and applies the turn when the promise settles.
//
// Module-level (not React state): the composer unmounts on navigate, so
// nothing in the component tree would survive the handoff.
import type { MessageTurn } from "@/lib/api"

export type InflightFirstSend = {
  conversationId: string
  pendingContent: string
  promise: Promise<MessageTurn>
}

let current: InflightFirstSend | null = null

export function beginInflightFirstSend(entry: InflightFirstSend) {
  current = entry
}

export function peekInflightFirstSend(conversationId: string): InflightFirstSend | null {
  if (current?.conversationId === conversationId) return current
  return null
}

export function clearInflightFirstSend(conversationId: string) {
  if (current?.conversationId === conversationId) current = null
}
