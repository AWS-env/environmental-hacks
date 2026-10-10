import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["test/**/*.test.ts"],
    // The contract-validator tests start a Python process per pair; a cold start on a loaded Windows
    // machine can exceed the 5 s default and fail a run that is otherwise green.
    testTimeout: 30000,
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
    },
  },
});
