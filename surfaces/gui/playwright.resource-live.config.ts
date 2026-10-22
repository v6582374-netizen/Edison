import { defineConfig, devices } from "@playwright/test";

// Opt-in real-machine resource measurement against an explicitly forwarded
// isolated Linux fixture. Uses one production frontend build for every arm.
const PORT = 5299;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "youtube-resource-measurement.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
