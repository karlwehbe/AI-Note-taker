// Acceptance tests: a real browser driving the real app against the real
// backend. The only thing faked is the model — the stack is started from
// docker-compose.test.yml, which points OPENAI_BASE_URL at a local stub, so
// journeys are deterministic without any code in app/ knowing.
//
// Bring the backend up first (see server/tests/system/conftest.py), then:
//   npm run test:e2e
import { defineConfig, devices } from "@playwright/test"

// 8001 is the stubbed stack from docker-compose.test.yml. Never 8000 — that
// is the dev API, wired to the real OpenAI and to real conversations, so a
// run pointed there would spend money and write into actual notes.
const API_URL = process.env.VITE_API_URL ?? "http://localhost:8001"

export default defineConfig({
  testDir: "./e2e",
  // The suite asserts on a shared backend, so parallel workers would race on
  // the conversation list and the single profile row.
  workers: 1,
  fullyParallel: false,
  // Generation goes through the stub, but the whole turn still crosses two
  // containers and a graph.
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:5174",
    // Traces are screencasts: each one holds a JPEG frame per captured
    // moment, so they are kept only for failures. Everything they write goes
    // to test-results/, which is gitignored and wiped at the start of a run.
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Its own dev server on 5174, and reuseExistingServer is off. Reusing a
    // server you already had running on 5173 would ignore the env below and
    // silently point the whole suite at the dev API.
    command: "npm run dev -- --port 5174 --strictPort",
    url: "http://localhost:5174",
    reuseExistingServer: false,
    timeout: 60_000,
    env: { VITE_API_URL: API_URL },
  },
})
