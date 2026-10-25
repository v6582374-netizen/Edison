import { type Page } from "@playwright/test";
import { test, expect } from "./fixtures";

async function openYouTube(page: Page, lang = "zh") {
  await page.addInitScript(lang => localStorage.setItem("openworker.lang", lang), lang);
  await page.route("**/v1/youtube/capability", async route => {
    const { capability } = route.request().postDataJSON();
    const results: Record<string, unknown> = {
      "library.list": { assets: [], total: 0 },
      "activity.list": { items: [], total: 0 },
      "collection.preferences": { sources: [{ channel_id: "one", title: "Design Notes" }], excluded_channels: [] },
      "connection.status": { configured: false, authorized: false, subscription_count: 0 },
      "activity.snapshot": { queued: 0, acquiring: 0, generating: 0, ready: 0, failed: 0, unavailable: 0, drain_paused: false, model_ready: true, batch: { completed: 0, limit: 100 } },
    };
    await route.fulfill({ json: { ok: true, result: results[capability] || {} } });
  });
  await page.goto("/");
  await page.getByTestId("nav-youtube").click();
}


test("YouTube changes modal content in place and restores its external trigger", async ({ page }) => {
  await openYouTube(page);
  const trigger = page.getByRole("button", { name: "订阅频道", exact: true });
  await trigger.click();
  const first = await page.getByRole("dialog").elementHandle();
  await page.getByRole("dialog").evaluate(el => el.getAnimations().forEach(a => a.finish()));
  await page.getByRole("dialog").getByRole("button", { name: "连接 YouTube", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "连接 YouTube", exact: true });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((el, old) => el === old, first)).toBe(true);
  expect(await dialog.evaluate(el => el.getAnimations().length)).toBe(0);
  // Hidden developer fields and disabled buttons cannot enter the tab cycle.
  await dialog.getByRole("button", { name: "关闭", exact: true }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByText("开发者设置", {exact: true})).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
  await page.getByTestId("nav-youtube").evaluate((el: HTMLElement) => el.focus());
  expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("YouTube backdrop ignores drags and cancelled pointers", async ({ page }) => {
  await openYouTube(page);
  await page.getByRole("button", { name: "订阅频道", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const box = (await dialog.boundingBox())!;
  await page.mouse.move(box.x + 20, box.y + 20);
  await page.mouse.down();
  await page.mouse.move(5, 5);
  await page.mouse.up();
  await expect(dialog).toBeVisible();
  const scrim = page.locator(".modal-scrim");
  await scrim.dispatchEvent("pointerdown", { pointerId: 7, button: 0 });
  await scrim.dispatchEvent("pointercancel", { pointerId: 7 });
  await scrim.dispatchEvent("pointerup", { pointerId: 7, button: 0 });
  await expect(dialog).toBeVisible();
  await page.mouse.click(5, 5);
  await expect(dialog).toHaveCount(0);
});

for (const lang of ["zh", "en"] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`modal layout remains readable in ${lang}/${theme}`, async ({ page }, testInfo) => {
      await page.emulateMedia({ colorScheme: theme });
      await openYouTube(page, lang);
      await page.getByRole("button", { name: lang === "zh" ? "订阅频道" : "Subscriptions", exact: true }).click();
      await page.getByRole("dialog").getByRole("button", { name: lang === "zh" ? "连接 YouTube" : "Connect YouTube", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByText(lang === "zh" ? "开发者设置" : "Developer settings", {exact: true}).click();
      for (const width of [800, 1100, 1440]) {
        await page.setViewportSize({ width, height: 640 });
        await expect(dialog).toBeVisible();
        const box = (await dialog.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(23);
        expect(box.y).toBeGreaterThanOrEqual(23);
        expect(box.x + box.width).toBeLessThanOrEqual(width - 23);
        expect(box.y + box.height).toBeLessThanOrEqual(617);
        expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
        const body = dialog.locator(".yp-dialogbody");
        await body.evaluate(el => { el.scrollTop = el.scrollHeight; });
        expect(await body.evaluate(el => el.scrollHeight <= el.clientHeight || el.scrollTop > 0)).toBe(true);
        await dialog.getByRole("button", { name: lang === "zh" ? "关闭" : "Close", exact: true }).focus();
        await expect(dialog.getByRole("button", { name: lang === "zh" ? "关闭" : "Close", exact: true })).toBeFocused();
        await page.screenshot({ path: testInfo.outputPath(`modal-${width}.png`) });
      }
    });
  }
}
