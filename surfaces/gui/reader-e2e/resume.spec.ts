import {test, expect, type Page, type APIRequestContext, type TestInfo} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {randomUUID} from "node:crypto";
import path from "node:path";

const token = {"X-OpenWorker-Token": "isolated-reader-e2e-token"};
const endpoint = (id: string) => `/api/v1/reader/books/${id}/reading`;
const state = async (request: APIRequestContext, id: string) => (await request.get(endpoint(id), {headers: token})).json();

test.beforeEach(async ({context}) => {
  await context.addInitScript(() => {
    if (window !== window.top) return;
    (window as any).__COWORKER_HTTP__ = location.origin + "/api";
    (window as any).__COWORKER_WS__ = location.origin.replace("http", "ws") + "/api";
    (window as any).__COWORKER_API_TOKEN__ = "isolated-reader-e2e-token";
    try {localStorage.setItem("openworker.lang", "zh");} catch {}
  });
});

async function openNew(page: Page, request: APIRequestContext, info: TestInfo, fixture = path.resolve("../../tests/fixtures/reader/epub3.epub")) {
  const before = (await (await request.get("/api/v1/reader/books", {headers: token})).json()).books.length;
  await page.goto("/?module=reader");
  // A distinct immutable book per case; ZIP permits these small trailing bytes.
  const bytes = Buffer.concat([await readFile(fixture), Buffer.from(info.testId + randomUUID())]);
  const imported = page.waitForResponse(response => response.url().endsWith("/v1/reader/books") && response.request().method() === "POST");
  await page.getByLabel("导入 EPUB", {exact: true}).setInputFiles({name:"resume.epub", mimeType:"application/epub+zip", buffer:bytes});
  const book = (await (await imported).json()).book;
  await expect(page.locator(".reader-book-row")).toHaveCount(before + 1);
  await page.locator(".reader-book-row").first().getByRole("button").click();
  await expect(page.getByRole("button", {name:"下一页", exact:true})).toBeEnabled();
  return book.id as string;
}

async function savedAction(page: Page, request: APIRequestContext, id: string, action: () => Promise<unknown>) {
  const previous = await state(request, id);
  await action();
  await expect.poll(async () => (await state(request,id)).revision).toBeGreaterThan(previous.revision);
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  return state(request,id);
}

async function verifyVisible(page: Page, record: any) {
  await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
  await expect.poll(() => page.evaluate(position => {
    const view = document.querySelector("foliate-view") as any;
    if (!view?.lastLocation) return false;
    const resolved = view.resolveCFI(position.cfi);
    const content = view.renderer.getContents().find((item:any) => item.index === resolved.index);
    if (!content) return false;
    const range = resolved.anchor(content.doc);
    // A saved quotation may cross a page boundary after a font/viewport change.
    // Verify its complete identity, and that its starting location is visible.
    return range.toString() === position.quote && (position.kind === "text"
      ? view.lastLocation.range.comparePoint(range.startContainer, range.startOffset) === 0
      : view.lastLocation.range.intersectsNode(range.startContainer));
  }, record.position)).toBe(true);
}

test("resume: exact text, preferences, reflow and furthest are durable", async ({page, request}, info) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const id = await openNew(page, request, info);
  const next = () => page.getByRole("button", {name:"下一页",exact:true}).click();
  await savedAction(page,request,id,next);
  let record = await savedAction(page,request,id,next);
  expect(record.position.offset).toBeGreaterThan(0);
  const beforeReflow = record.position;
  record = await savedAction(page,request,id,() => page.getByLabel("字号").selectOption("28"));
  record = await savedAction(page,request,id,() => page.getByRole("button",{name:"目录",exact:true}).click());
  expect(record.position).toEqual(beforeReflow);
  await page.setViewportSize({width:760,height:900});
  await page.waitForTimeout(600);
  await verifyVisible(page,record);
  expect((await state(request,id)).revision).toBe(record.revision);
  await page.reload();
  await verifyVisible(page,record);
  await expect(page.getByLabel("字号")).toHaveValue("28");
  await expect(page.getByRole("button",{name:"目录",exact:true})).toHaveAttribute("aria-expanded","false");
  expect((await state(request,id)).revision).toBe(record.revision); // restore is not a write
  await page.screenshot({path:info.outputPath("resumed-narrow.png")});
  const url = page.url();
  await page.getByRole("button",{name:"← 书库"}).click();
  await page.goto(url); await verifyVisible(page,record);
  await savedAction(page,request,id,() => page.getByRole("button",{name:"目录",exact:true}).click());
  const furthest = await savedAction(page,request,id,() => page.getByRole("button",{name:"注释",exact:true}).click());
  const back = await savedAction(page,request,id,() => page.getByRole("button",{name:"上一页",exact:true}).click());
  expect(back.position.index).toBe(0);
  expect(back.furthest).toEqual(furthest.furthest);
  expect(back.progress).toBe(furthest.progress);
  await page.reload(); await verifyVisible(page, back);
  expect(errors).toEqual([]);
});

test("resume: two pages cannot silently overwrite each other", async ({page,context,request},info) => {
  const id = await openNew(page,request,info);
  await savedAction(page,request,id,() => page.getByRole("button",{name:"下一页",exact:true}).click());
  const other = await context.newPage(); await other.goto(page.url());
  await expect(other.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  const current = await savedAction(page,request,id,() => page.getByRole("button",{name:"下一页",exact:true}).click());
  await other.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(other.getByRole("alert")).toContainText("另一页面");
  expect(await state(request,id)).toEqual(current);
  await other.getByTestId("nav-youtube").click();
  await expect(other.locator("foliate-view")).toBeVisible(); // internal navigation preserves unsaved state
  await other.getByRole("button",{name:"采用服务器记录"}).click();
  await verifyVisible(other,current);
  expect(await state(request,id)).toEqual(current);
  await other.close();
});

test("resume: failed save keeps local position and fast-forward progress for retry", async ({page,request},info) => {
  const id = await openNew(page,request,info);
  await page.route("**/reading", route => route.request().method() === "PUT" ? route.abort("failed") : route.continue());
  await page.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("保存失败");
  const prompt = page.waitForEvent("dialog");
  const reload = page.evaluate(() => location.reload()).catch(() => null);
  const dialog = await prompt;
  expect(dialog.type()).toBe("beforeunload");
  await dialog.dismiss(); await reload;
  await expect(page.getByRole("alert")).toContainText("保存失败");
  await page.getByRole("button",{name:"注释",exact:true}).click();
  await expect.poll(() => page.evaluate(() => (document.querySelector("foliate-view") as any)?.lastLocation?.section.current)).toBe(1);
  await page.getByRole("button",{name:"上一页",exact:true}).click();
  await expect.poll(() => page.evaluate(() => (document.querySelector("foliate-view") as any)?.lastLocation?.section.current)).toBe(0);
  expect((await state(request,id)).revision).toBe(0);
  await page.getByRole("button",{name:"← 书库"}).click();
  await expect(page.locator("foliate-view")).toBeVisible();
  await page.screenshot({path:info.outputPath("save-failure.png")});
  await page.unroute("**/reading");
  await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  const record = await state(request,id);
  expect(record.position.index).toBe(0); expect(record.furthest.index).toBe(1);
  await page.reload(); await verifyVisible(page,record);
});

test("resume: a lost committed response retries without duplicate writes", async ({page,request},info) => {
  const id = await openNew(page,request,info);
  let dropped = false;
  await page.route("**/reading", async route => {
    if (route.request().method() !== "PUT" || dropped) return route.continue();
    dropped = true;
    const response = await route.fetch(); expect(response.status()).toBe(200);
    await route.abort("failed");
  });
  await page.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("保存失败");
  const committed = await state(request,id); expect(committed.revision).toBe(1);
  await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  expect(await state(request,id)).toEqual(committed);
});

test("resume: mismatched text is preserved and never silently replaced by a percentage", async ({page,request},info) => {
  const id = await openNew(page,request,info);
  const record = await savedAction(page,request,id,() => page.getByRole("button",{name:"下一页",exact:true}).click());
  const response = await request.put(endpoint(id), {headers:token, data:{
    ...record, position:{...record.position,quote:"This quotation is deliberately wrong"}, operation_id:randomUUID(),
  }});
  expect(response.status()).toBe(200);
  const invalid = await state(request,id);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("无法核对");
  expect(await state(request,id)).toEqual(invalid);
  const url = page.url();
  await page.getByRole("button",{name:"← 书库"}).click();
  await expect(page.getByRole("heading",{name:"Reader",exact:true})).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("alert")).toContainText("无法核对");
  await page.getByRole("button",{name:"从头重新记录"}).click();
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  const restarted = await state(request,id);
  expect(restarted.revision).toBe(invalid.revision+1);
  expect(restarted.position.quote).not.toBe(invalid.position.quote);
  expect(restarted.furthest).toEqual(invalid.furthest);
});

test("resume: actual expired token fails visibly and can retry after sign-in", async ({page,request},info) => {
  const id = await openNew(page,request,info);
  await page.evaluate(() => {(window as any).__COWORKER_API_TOKEN__ = "expired-test-token";});
  await page.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(page.getByRole("alert")).toContainText("登录已失效");
  expect((await state(request,id)).revision).toBe(0);
  await page.evaluate(() => {(window as any).__COWORKER_API_TOKEN__ = "isolated-reader-e2e-token";});
  await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  expect((await state(request,id)).revision).toBe(1);
});

for (const profile of ["epub2", "vertical", "rtl", "font"]) {
  test(`resume profile: ${profile} restores its exact anchor`, async ({page,request},info) => {
    const layoutDirectory = process.env.EDISON_READER_LAYOUT_DIR;
    test.skip(profile !== "epub2" && !layoutDirectory, "Optional stage-0 layout fixtures stay in the investigation directory");
    const fixture = profile === "epub2" ? path.resolve("../../tests/fixtures/reader/epub2.epub") : path.join(layoutDirectory!, `${profile}.epub`);
    const id = await openNew(page,request,info,fixture);
    await savedAction(page,request,id,() => page.locator(".reader-reading nav button").first().click());
    const record = await savedAction(page,request,id,() => page.getByLabel("字号").selectOption("28"));
    const layout = await page.evaluate(async () => {
      const doc = (document.querySelector("foliate-view") as any).renderer.getContents()[0].doc;
      await doc.fonts.ready;
      return {writing:getComputedStyle(doc.body).writingMode, direction:getComputedStyle(doc.body).direction,
        font:[...doc.fonts].some((font:any) => font.family === "EmbeddedProbe" && font.status === "loaded")};
    });
    if (profile === "vertical") expect(layout.writing).toContain("vertical");
    if (profile === "rtl") expect(layout.direction).toBe("rtl");
    if (profile === "font") expect(layout.font).toBe(true);
    await page.reload();
    await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
    await verifyVisible(page,record);
    await expect(page.getByLabel("字号")).toHaveValue("28");
    expect(await state(request,id)).toEqual(record);
    await page.screenshot({path:info.outputPath(`${profile}-resumed.png`)});
  });
}
