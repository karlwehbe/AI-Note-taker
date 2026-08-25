// Merged onto vite.config.ts rather than replacing it: a standalone config
// would drop the "@/" alias, and the modules under test import through it.
//
// The `test.include` narrowing is what keeps vitest out of e2e/ — those specs
// import @playwright/test and are run by `npm run test:e2e` against a real
// browser and a real stack.
import { defineConfig, mergeConfig } from "vitest/config"

import viteConfig from "./vite.config.ts"

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      exclude: ["e2e/**", "node_modules/**", "dist/**"],
    },
  }),
)
