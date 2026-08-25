// Sidebar's conversation list — fetched independently of route navigation
// (the sidebar persists across route changes), with a refetch() callers
// trigger after creating/sending/deleting so the list stays in sync.
import { useCallback, useEffect, useState } from "react"

import { api, type Conversation } from "@/lib/api"

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  // True only until the first list fetch settles — later refetches keep the
  // existing rows visible instead of flashing skeletons.
  const [loading, setLoading] = useState(true)

  const refetch = useCallback(async () => {
    try {
      setConversations(await api.listConversations())
    } catch {
      // Sidebar just stays empty/stale — not worth surfacing an error for.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refetch()
  }, [refetch])

  return { conversations, loading, refetch }
}
