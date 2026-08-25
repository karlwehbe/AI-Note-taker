// Shares the sidebar's conversation list + a refetch() trigger across the
// app, so sending a message from a route component can make the sidebar
// re-fetch and show the new/updated conversation without prop-drilling.
import { createContext, useContext } from "react"

import { useConversations } from "@/lib/use-conversations"
import type { Conversation } from "@/lib/api"

type ConversationsContextValue = {
  conversations: Conversation[]
  loading: boolean
  refetch: () => Promise<void>
}

const ConversationsContext = createContext<ConversationsContextValue | null>(null)

export function ConversationsProvider({ children }: { children: React.ReactNode }) {
  const value = useConversations()
  return <ConversationsContext value={value}>{children}</ConversationsContext>
}

export function useConversationsContext() {
  const ctx = useContext(ConversationsContext)
  if (!ctx) {
    throw new Error("useConversationsContext must be used within ConversationsProvider")
  }
  return ctx
}
