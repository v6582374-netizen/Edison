import {test, expect} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import path from "node:path";

const original = path.resolve("../../tests/fixtures/reader/epub3.epub");
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

test("real server: library → import → classify → reload → read → original", async ({page, context, request}, info) => {
  await context.addInitScript(() => {
    if (window !== window.top) return;
    (window as any).__COWORKER_HTTP__ = location.origin + "/api";
    (window as any).__COWORKER_WS__ = location.origin.replace("http", "ws") + "/api";
    (window as any).__COWORKER_API_TOKEN__ = "isolated-reader-e2e-token";
    try {localStorage.setItem("openworker.lang", "zh");} catch { /* initial about:blank */ }
  });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByTestId("nav-reader").click();
  await expect(page.getByRole("heading", {name: "Reader", exact: true})).toBeVisible();
  const importing = page.waitForResponse(response => response.url().endsWith("/v1/reader/books") && response.request().method() === "POST");
  await page.getByLabel("导入 EPUB", {exact: true}).setInputFiles(original);
  expect((await importing).ok()).toBe(true);
  // Query the durable identity; Chromium may evict no-store POST bodies from
  // its inspector cache before Playwright reads them.
  const digest = sha(await readFile(original));
  const books = await (await page.request.get("/api/v1/reader/books", {headers:{"X-OpenWorker-Token":"isolated-reader-e2e-token"}})).json();
  const book = books.books.find((item:any) => item.sha256 === digest);
  const row = page.locator(`[data-book-id="${book.id}"]`);
  await expect(row).toBeVisible();
  const placement = row.getByRole("combobox");
  await placement.selectOption("inbox");
  await expect(placement).toBeEnabled();
  await placement.selectOption("later");
  await expect(placement).toBeEnabled();
  await page.reload();
  await expect(row.getByRole("combobox")).toHaveValue("later");
  const libraryCount = await page.locator(".reader-book-row").count();
  await page.getByLabel("导入 EPUB", {exact:true}).setInputFiles(original);
  await expect(page.getByRole("status")).toHaveText("这本书已在书库中，原有归类已保留");
  await expect(row).toHaveCount(1);
  await expect(row.getByRole("combobox")).toHaveValue("later");
  await page.screenshot({path:info.outputPath("library-wide.png")});

  // Two real pages hold the same revision; the second mutation must not overwrite.
  const other = await context.newPage();
  await other.goto("/?module=reader");
  const otherPlacement = other.locator(`[data-book-id="${book.id}"]`).getByRole("combobox");
  await expect(otherPlacement).toHaveValue("later");
  await row.getByRole("combobox").selectOption("archive");
  await expect(row.getByRole("combobox")).toBeEnabled();
  await otherPlacement.selectOption("inbox");
  await expect(other.getByRole("alert")).toBeVisible();
  await other.getByRole("button", {name:"刷新重试"}).click();
  await expect(otherPlacement).toHaveValue("archive");
  await other.close();

  await row.getByRole("button").click();
  await expect(page).toHaveURL(/module=reader&book=/);
  await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
  await page.getByRole("button", {name:"第一章",exact:true}).click();
  await page.waitForFunction(() => {
    const v = document.querySelector("foliate-view") as any;
    return v?.renderer?.getContents().some(({doc}:any) => doc.querySelector("#target"));
  });
  expect(await page.evaluate(() => (window as any).__epubExecuted)).toBeUndefined();
  await page.getByLabel("字号").selectOption("28");
  await page.waitForTimeout(600); // let pagination/font callbacks settle before visual evidence
  await page.screenshot({path:info.outputPath("reading-wide.png")});
  const bookURL = page.url();
  await page.reload();
  await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
  expect(page.url()).toBe(bookURL);
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", {name:"下载原书",exact:true}).click();
  const download = await downloading;
  expect(sha(await readFile((await download.path())!))).toBe(sha(await readFile(original)));
  await page.setViewportSize({width:760,height:900});
  await page.waitForTimeout(600);
  await page.screenshot({path:info.outputPath("reading-narrow.png")});
  await page.getByRole("button", {name:"← 书库"}).click();
  await page.getByLabel("导入 EPUB", {exact:true}).setInputFiles({name:"broken.epub",mimeType:"application/epub+zip",buffer:Buffer.from("broken zip")});
  await expect(page.getByRole("alert")).toContainText("损坏");
  await expect(page.locator(".reader-book-row")).toHaveCount(libraryCount);
  expect((await request.get("/api/v1/reader/books")).status()).toBe(401);
  for (let attempt = 0; attempt < 3; attempt++) {
    await row.getByRole("button").click();
    await page.getByRole("button", {name:"← 书库"}).click();
  }
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
});

test("optional private EPUB: real original survives import and reopen", async ({page, context}, info) => {
  const file = process.env.EDISON_READER_TEST_EPUB;
  test.skip(!file, "Private books stay outside the repository");
  await context.addInitScript(() => {
    if (window !== window.top) return;
    (window as any).__COWORKER_HTTP__ = location.origin + "/api";
    (window as any).__COWORKER_WS__ = location.origin.replace("http", "ws") + "/api";
    (window as any).__COWORKER_API_TOKEN__ = "isolated-reader-e2e-token";
    try {localStorage.setItem("openworker.lang", "zh");} catch {}
  });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/?module=reader");
  const importing = page.waitForResponse(response => response.url().endsWith("/v1/reader/books") && response.request().method() === "POST");
  await page.getByLabel("导入 EPUB", {exact:true}).setInputFiles(file!);
  expect((await importing).ok()).toBe(true);
  const digest = sha(await readFile(file!));
  const books = await (await page.request.get("/api/v1/reader/books", {headers:{"X-OpenWorker-Token":"isolated-reader-e2e-token"}})).json();
  const book = books.books.find((item:any) => item.sha256 === digest);
  await page.locator(`[data-book-id="${book.id}"]`).getByRole("button").click();
  await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
  await page.locator(".reader-reading nav button").nth(5).click();
  await page.waitForFunction(() => {
    const view = document.querySelector("foliate-view") as any;
    return view?.renderer?.getContents().some(({doc}:any) => doc.body.textContent.length > 100);
  });
  await page.getByLabel("字号").selectOption("28");
  await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
  const reading = await (await page.request.get(`/api/v1/reader/books/${book.id}/reading`, {headers:{"X-OpenWorker-Token":"isolated-reader-e2e-token"}})).json();
  await page.screenshot({path:info.outputPath("private-book-local-only.png")});
  await page.reload();
  await expect(page.getByRole("button", {name:"下一页",exact:true})).toBeEnabled();
  expect(await page.evaluate(position => (document.querySelector("foliate-view") as any).lastLocation.range.toString().includes(position.quote), reading.position)).toBe(true);
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", {name:"下载原书",exact:true}).click();
  expect(sha(await readFile((await (await downloading).path())!))).toBe(sha(await readFile(file!)));
  await page.getByRole("button", {name:"← 书库"}).click();
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
});
