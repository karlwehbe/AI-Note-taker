// Client entry point that Vite's index.html loads to bootstrap the SPA: sets
// up the TanStack Router instance (using the route tree generated from
// src/routes/) and renders it into #root inside React.StrictMode, which
// double-invokes effects/renders in dev to surface side-effect bugs early.
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { createRouter, RouterProvider } from "@tanstack/react-router"

import "katex/dist/katex.min.css"
import "./index.css"
import { routeTree } from "./routeTree.gen"

const router = createRouter({ routeTree })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

declare module "@tanstack/history" {
  interface HistoryState {
    /** Soft-open notes when landing from a new-chat first reply. */
    animateNotesOpen?: boolean
    /** Stream-reveal the latest assistant message on that landing. */
    streamAssistant?: boolean
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
