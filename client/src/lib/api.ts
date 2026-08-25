// Thin fetch wrappers for the server's /conversations API — keeps the actual
// endpoint URLs and response shapes in one place instead of duplicated
// across the sidebar, composer, and conversation page.
export const apiUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

// Same host as apiUrl, but ws(s):// instead of http(s):// — used to connect
// to the live-transcription proxy at /ws/transcribe.
export const wsApiUrl = apiUrl.replace(/^http/, "ws")

export type Message = {
  id: string
  role: "user" | "assistant"
  content: string
  filename: string | null
  created_at: string
}

export type Conversation = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type ConversationDetail = Conversation & {
  messages: Message[]
  note_content: string | null
  draft_transcript: string | null
}

export type MessageTurn = {
  user_message: Message
  assistant_message: Message
  note_content: string | null
  title: string
}

// The personal context layer: a short profile compiled by an LLM into a
// description of the user that rides along on every note/chat prompt.
// See server/app/api/profile.py.
export type ProfileFields = {
  occupation: string
  background_level: string
  education_level: string
  notes_purpose: string[]
  emphasize: string[]
  extra: string
}

export type UserProfileState = {
  name: string
  fields: ProfileFields
  // null = no personal layer; notes generate normally without one.
  compiled_prompt: string | null
  // true once the user rewrites compiled_prompt by hand — changing a form
  // field then requires confirmation before it's replaced.
  is_edited: boolean
  has_profile: boolean
  // Set alongside a null compiled_prompt when compilation was attempted and
  // failed — distinguishes that from a profile that was never filled in.
  compile_failed_at: string | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${apiUrl}${path}`, init)
  } catch {
    // fetch() itself throwing means the request never reached the server
    // (offline, DNS failure, connection refused) — the raw browser message
    // for that ("Failed to fetch", "NetworkError...") isn't something to
    // show someone using the app.
    throw new Error("Can't reach the server — check your connection and try again.")
  }
  if (!res.ok) {
    const message = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    throw new Error(message || `Something went wrong — please try again. (error ${res.status})`)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  listConversations: () => request<Conversation[]>("/conversations"),
  createConversation: () => request<Conversation>("/conversations", { method: "POST" }),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  deleteConversation: (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
  sendMessage: (conversationId: string, audio: Blob, filename: string, transcript?: string) => {
    const formData = new FormData()
    formData.append("file", audio, filename)
    // Set when audio was already transcribed live via /ws/transcribe — lets
    // the server skip a redundant batch transcription call.
    if (transcript !== undefined) formData.append("transcript", transcript)
    return request<MessageTurn>(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: formData,
    })
  },
  sendTextMessage: (conversationId: string, text: string) => {
    const formData = new FormData()
    formData.append("transcript", text)
    return request<MessageTurn>(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: formData,
    })
  },
  // Fire-and-forget autosave of the transcript captured so far while a
  // recording is in progress — see conversations.py's save_draft.
  // keepalive: true so the request still completes when the composer
  // unmounts mid-navigation (otherwise the browser may cancel it).
  saveDraftTranscript: (conversationId: string, transcript: string) =>
    request<void>(`/conversations/${conversationId}/draft`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript }),
      keepalive: true,
    }),
  getProfile: () => request<UserProfileState>("/profile"),
  // regenerate: pass true only after the user has confirmed discarding a
  // hand-edited prompt — otherwise the server leaves their wording alone.
  saveProfile: (input: { name: string; fields: ProfileFields; regenerate?: boolean }) =>
    request<UserProfileState>("/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  regenerateProfilePrompt: () => request<UserProfileState>("/profile/regenerate", { method: "POST" }),
  saveCompiledPrompt: (compiled_prompt: string) =>
    request<UserProfileState>("/profile/prompt", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ compiled_prompt }),
    }),
  deleteProfile: () => request<void>("/profile", { method: "DELETE" }),
}
