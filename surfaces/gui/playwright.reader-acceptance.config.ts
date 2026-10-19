import {defineConfig} from "@playwright/test";

// Normal HTTPS origin, cookie login and the production server CLI, with isolated data.
// Caddy is an explicit prerequisite; this suite never installs a trust certificate.
export default defineConfig({
  testDir: "./reader-acceptance",
  outputDir: "../../.scratch/reader-acceptance",
  fullyParallel: false, workers: 1, timeout: 120_000,
  reporter: [["list"]],
  use: {baseURL: "https://localhost:8943", ignoreHTTPSErrors: true, trace: "retain-on-failure"},
  projects: ["chromium", "firefox", "webkit"].map(browserName => ({
    name: browserName, use: {browserName: browserName as "chromium" | "firefox" | "webkit"},
  })),
});
