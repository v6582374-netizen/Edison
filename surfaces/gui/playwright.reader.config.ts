import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./reader-e2e",
  outputDir: "../../.scratch/reader-e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: [["list"]],
  use: {baseURL: "http://127.0.0.1:8791", trace: "retain-on-failure"},
  projects: ["chromium", "firefox", "webkit"].map(browserName => ({
    name: browserName, use: {browserName: browserName as "chromium" | "firefox" | "webkit",
      launchOptions: {firefoxUserPrefs: {"dom.disable_beforeunload": false}}},
  })),
  webServer: {
    command: "../../.venv/bin/python ../../scripts/reader_e2e_server.py",
    url: "http://127.0.0.1:8791", reuseExistingServer: false, timeout: 60_000,
  },
});
