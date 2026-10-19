import { defineConfig, devices } from "@playwright/test";
import base from "./playwright.config";
export default defineConfig({
  ...base, testMatch: ["youtube-content.spec.ts", "youtube-manual.spec.ts", "youtube-official.spec.ts", "youtube-api.spec.ts", "youtube-freshness.spec.ts", "youtube-documents.spec.ts", "youtube-progress-live.spec.ts", "youtube-shorts-live.spec.ts", "youtube-rate-live.spec.ts"], workers: 1,
  projects: [
    {name: "chromium", use: {...devices["Desktop Chrome"]}},
    {name: "webkit", use: {...devices["Desktop Safari"]}},
  ],
});
