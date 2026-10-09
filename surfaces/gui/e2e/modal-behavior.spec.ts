import { type Page } from "@playwright/test";
import { test, expect } from "./fixtures";

async function openLibrary(page: Page, reply?: (capability: string, args: Record<string, unknown>) => Promise<unknown>) {
  await page.addInitScript(() => localStorage.setItem("openworker.lang", "zh"));
  await page.route("**/v1/minimalism/capability", async route => {
    const { capability, arguments: args = {} } = route.request().postDataJSON();
    const supplied = await reply?.(capability, args);
    if (supplied !== undefined) {
      await route.fulfill({ json: { ok: true, result: supplied } });
      return;
    }
    const result = capability === "library.snapshot" ? {
      assets: [{ id: "camera", name: "随身相机", memoryBlocks: [], archivePhotoPaths: [],
        coverProvenance: "none", customMetadata: {}, createdAt: "2026-01-01", updatedAt: "2026-01-01", syncVersion: 1 }],
      collections: [{ id: "archive", name: "归档", role: "archive", displayOrder: 0 }],
      preferences: { coverPromptTemplate: "", visibleMetadataFields: ["brand", "material"] },
      migration: {}, image_ready: true,
    } : capability === "image.settings" ? { ready: true } : capability === "cover.generate" ? {
      preview: "preview-one", mime: "image/png", used_reference: false,
      data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==",
    } : {};
    await route.fulfill({ json: { ok: true, result } });
  });
  await page.goto("/");
  await page.getByTestId("nav-items").click();
}

test("new object keeps keyboard focus inside and returns it after Escape", async ({ page }) => {
  await openLibrary(page);
  const add = page.getByRole("button", { name: "添加物品", exact: true }).first();
  await add.click();
  const dialog = page.getByRole("dialog", { name: "添加物品", exact: true });
  const name = dialog.getByRole("textbox", { name: "名称", exact: true });
  await expect(name).toBeFocused();
  await dialog.getByRole("button", { name: "保存", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(name).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "保存", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(add).toBeFocused();
});


test("closing a cover preview does not wait for resource cleanup", async ({ page }) => {
  let release!: () => void;
  const cleanup = new Promise<void>(resolve => { release = resolve; });
  await openLibrary(page, async capability => {
    if (capability === "cover.discard") { await cleanup; return {}; }
  });
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  const generate = page.getByRole("button", { name: "生成封面", exact: true });
  await generate.click();
  const preview = page.getByRole("dialog", { name: "封面预览", exact: true });
  await expect(preview).toBeVisible();
  try {
    await page.keyboard.press("Escape");
    await expect(preview).toHaveCount(0, { timeout: 500 });
    await expect(generate).toBeFocused();
  } finally { release(); }
});

test("pointer transitions can reverse, while Escape and keyboard entry stay immediate", async ({ page }) => {
  await page.addInitScript(() => {
    const animate = Element.prototype.animate;
    Element.prototype.animate = function (...args: Parameters<Element["animate"]>) {
      const animation = animate.apply(this, args);
      if (this.matches(".modal-surface,.modal-backdrop")) animation.playbackRate = 0.1;
      return animation;
    };
  });
  await openLibrary(page);
  const add = page.getByRole("button", { name: "添加物品", exact: true }).first();
  await add.click();
  const dialog = page.getByRole("dialog", { name: "添加物品", exact: true });
  await expect.poll(() => dialog.evaluate(el => el.getAnimations().length)).toBe(1);
  const timing = await dialog.evaluate(el => el.getAnimations()[0].effect!.getTiming());
  expect(timing.duration).toBe(200);
  expect(timing.easing).toBe("cubic-bezier(0.23, 1, 0.32, 1)");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(add).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(el => el.getAnimations().length)).toBe(0);
  await page.keyboard.press("Escape");
  await add.click();
  // Complete entry so pointer actions can hit its controls, then inspect the slow exit.
  await dialog.evaluate(el => el.getAnimations().forEach(animation => animation.finish()));
  await dialog.evaluate(el => {
    const samples: { opacity: number; scale: number }[] = [];
    (window as any).modalSamples = samples;
    (window as any).samplingModal = true;
    const sample = () => {
      if (!(window as any).samplingModal || !el.isConnected) return;
      const style = getComputedStyle(el);
      samples.push({ opacity: Number(style.opacity), scale: new DOMMatrixReadOnly(style.transform).a });
      requestAnimationFrame(sample);
    };
    sample();
  });
  const original = await dialog.elementHandle();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const fading = page.locator('[data-modal-state="closing"] .modal-surface');
  await expect(fading).toHaveCount(1);
  const before = await fading.evaluate(el => Number(getComputedStyle(el).opacity));
  await add.click();
  await expect(dialog).toBeVisible();
  const after = await dialog.evaluate(el => Number(getComputedStyle(el).opacity));
  expect(after).toBeGreaterThan(before - 0.2);
  expect(after).toBeGreaterThan(0.5);
  expect(await dialog.evaluate((el, old) => el === old, original)).toBe(true);
  const samples = await page.evaluate(async () => {
    for (let i = 0; i < 5; i++) await new Promise(requestAnimationFrame);
    (window as any).samplingModal = false;
    return (window as any).modalSamples as { opacity: number; scale: number }[];
  });
  expect(samples.length).toBeGreaterThan(5);
  for (let i = 1; i < samples.length; i++) {
    expect(Math.abs(samples[i].opacity - samples[i - 1].opacity)).toBeLessThan(0.15);
    expect(Math.abs(samples[i].scale - samples[i - 1].scale)).toBeLessThan(0.005);
  }
  await dialog.evaluate(el => el.getAnimations().forEach(animation => animation.finish()));
  await expect(dialog).toBeVisible();
});

test("reduced motion removes scale, including when the preference changes mid-flight", async ({ page }) => {
  await page.addInitScript(() => {
    const animate = Element.prototype.animate;
    Element.prototype.animate = function (...args: Parameters<Element["animate"]>) {
      const animation = animate.apply(this, args);
      if (this.matches(".modal-surface,.modal-backdrop")) animation.playbackRate = 0.1;
      return animation;
    };
  });
  await openLibrary(page);
  await page.getByRole("button", { name: "添加物品", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "添加物品", exact: true });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(dialog).toHaveCSS("transform", "none");
  const frames = await dialog.evaluate(el => (el.getAnimations()[0].effect as KeyframeEffect).getKeyframes());
  expect(frames.every(frame => !("transform" in frame))).toBe(true);
  expect(frames[0].opacity).not.toBe(frames[frames.length - 1].opacity);
  await page.keyboard.press("Escape");
  await expect(page.locator(".modal-scrim")).toHaveCount(0);
});

test("leaving a preserved library dismisses its modal and preserves the memory draft", async ({ page }) => {
  await openLibrary(page);
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  await page.getByRole("button", { name: "添加记忆", exact: true }).click();
  await page.getByRole("textbox", { name: "记忆 1", exact: true }).fill("保留这段未保存的记忆");
  await page.getByRole("button", { name: "生成封面", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "封面预览" })).toBeVisible();
  await page.keyboard.press("Meta+,");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".modal-scrim")).toHaveCount(0);
  expect(await page.locator("#root").evaluate(el => el.hasAttribute("inert"))).toBe(false);
  await page.getByTestId("nav-items").click();
  await expect(page.getByRole("textbox", { name: "记忆 1", exact: true })).toHaveValue("保留这段未保存的记忆");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("late cover generation is discarded after navigating away", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const discarded: unknown[] = [];
  await openLibrary(page, async (capability, args) => {
    if (capability === "cover.generate") {
      await pending;
      return { preview: "late-preview", mime: "image/png", data: "", used_reference: false };
    }
    if (capability === "cover.discard") { discarded.push(args.preview); return {}; }
  });
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  const request = page.waitForRequest(r => r.url().endsWith("/v1/minimalism/capability") && r.postDataJSON().capability === "cover.generate");
  await page.getByRole("button", { name: "生成封面", exact: true }).click();
  await request;
  await page.keyboard.press("Meta+,");
  release();
  await expect.poll(() => discarded).toContain("late-preview");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByTestId("nav-items").click();
  await expect(page.getByRole("button", { name: "生成封面", exact: true })).toBeEnabled();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("regenerating keeps the preview open and a cancelled result cannot reopen it", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  const discarded: unknown[] = [];
  await openLibrary(page, async (capability, args) => {
    if (capability === "cover.generate" && ++requests > 1) {
      await pending;
      return { preview: "replacement", mime: "image/png", data: "", used_reference: false };
    }
    if (capability === "cover.discard") { discarded.push(args.preview); return {}; }
  });
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  await page.getByRole("button", { name: "生成封面", exact: true }).click();
  const preview = page.getByRole("dialog", { name: "封面预览" });
  await expect(preview).toBeVisible();
  const request = page.waitForRequest(r => r.url().endsWith("/v1/minimalism/capability") && r.postDataJSON().capability === "cover.generate");
  await preview.getByRole("button", { name: "重新生成", exact: true }).click();
  await request;
  await expect(preview).toBeVisible();
  await expect(preview.getByRole("button", { name: "采用封面", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  release();
  await expect.poll(() => discarded).toContain("replacement");
  await expect(preview).toHaveCount(0);
});

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
  await expect(dialog.locator("summary")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
  await page.getByTestId("nav-items").evaluate((el: HTMLElement) => el.focus());
  expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("a failed save remains readable inside the active modal", async ({ page }) => {
  await openLibrary(page);
  await page.route("**/v1/minimalism/capability", async route => {
    if (route.request().postDataJSON().capability !== "asset.create") return route.fallback();
    await route.fulfill({ json: { ok: false, error: "无法保存，请重试。" } });
  });
  await page.getByRole("button", { name: "添加物品", exact: true }).first().click();
  const dialog = page.getByRole("dialog", { name: "添加物品" });
  await dialog.getByRole("textbox", { name: "名称", exact: true }).fill("相机");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("无法保存，请重试。");
  await expect(dialog.getByRole("textbox", { name: "名称", exact: true })).toHaveValue("相机");
});

test("a failed cover adoption stays readable inside its preview", async ({ page }) => {
  await openLibrary(page);
  await page.route("**/v1/minimalism/capability", async route => {
    if (route.request().postDataJSON().capability !== "asset.cover.apply") return route.fallback();
    await route.fulfill({ json: { ok: false, error: "无法采用封面，请重试。" } });
  });
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  await page.getByRole("button", { name: "生成封面", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "封面预览" });
  await dialog.getByRole("button", { name: "采用封面", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("无法采用封面，请重试。");
  await expect(dialog.getByRole("button", { name: "采用封面", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("empty focus cycle and new background portals restore their original isolation", async ({ page }) => {
  await openLibrary(page);
  await page.evaluate(() => {
    document.body.style.overflow = "clip";
    const existing = document.createElement("aside");
    existing.id = "already-inert";
    existing.inert = true;
    document.body.append(existing);
  });
  const trigger = page.getByRole("button", { name: "添加物品", exact: true }).first();
  await trigger.focus();
  await page.keyboard.press("Space");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate(el => el.getAnimations().length)).toBe(0);
  await page.evaluate(() => {
    const portal = document.createElement("aside");
    portal.id = "late-portal";
    portal.innerHTML = '<button>External notification</button>';
    document.body.append(portal);
  });
  await expect(page.locator("#late-portal")).toHaveAttribute("inert", "");
  await dialog.evaluate(el => {
    el.querySelectorAll<HTMLInputElement | HTMLButtonElement>("input,button").forEach(control => { control.disabled = true; });
  });
  await page.keyboard.press("Tab");
  await expect(dialog).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(page.locator("#late-portal")).not.toHaveAttribute("inert");
  await expect(page.locator("#already-inert")).toHaveAttribute("inert", "");
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("clip");
});

test("finishing an old discard never clears the next preview", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let generated = 0;
  const discarded: unknown[] = [];
  await openLibrary(page, async (capability, args) => {
    if (capability === "cover.generate") return {
      preview: `preview-${++generated}`, mime: "image/png", data: "", used_reference: false,
    };
    if (capability === "cover.discard") {
      discarded.push(args.preview);
      if (args.preview === "preview-1") await pending;
      return {};
    }
  });
  await page.getByRole("button", { name: "打开 随身相机" }).click();
  const generate = page.getByRole("button", { name: "生成封面", exact: true });
  await generate.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await generate.click();
  const dialog = page.getByRole("dialog", { name: "封面预览" });
  await expect(dialog).toBeVisible();
  const response = page.waitForResponse(r => r.url().endsWith("/v1/minimalism/capability") && r.request().postDataJSON().capability === "cover.discard");
  release();
  await response;
  await expect(dialog).toBeVisible();
  expect(discarded).toEqual(["preview-1"]);
  await page.keyboard.press("Escape");
  await expect.poll(() => discarded).toEqual(["preview-1", "preview-2"]);
});

test("saving twice while pending creates only one object", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let saves = 0;
  await openLibrary(page, async capability => {
    if (capability === "asset.create") { saves++; await pending; return { id: "camera" }; }
  });
  await page.getByRole("button", { name: "添加物品", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill("Camera");
  await page.keyboard.press("Enter");
  await expect(dialog.getByRole("button", { name: "保存中…" })).toBeDisabled();
  await page.keyboard.press("Enter");
  expect(saves).toBe(1);
  release();
  await expect(dialog).toHaveCount(0);
  expect(saves).toBe(1);
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
      await dialog.locator("summary").click();
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

test("a cancelled save cannot close a newer form", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await openLibrary(page, async capability => {
    if (capability === "asset.create") { await pending; return { id: "camera" }; }
  });
  const add = page.getByRole("button", { name: "添加物品", exact: true }).first();
  await add.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill("First object");
  await page.keyboard.press("Enter");
  await expect(dialog.getByRole("button", { name: "保存中…" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await add.click();
  await dialog.getByRole("textbox").fill("Next object");
  const response = page.waitForResponse(r => r.url().endsWith("/v1/minimalism/capability") && r.request().postDataJSON().capability === "asset.create");
  release();
  await response;
  await expect(dialog.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("textbox")).toHaveValue("Next object");
});
