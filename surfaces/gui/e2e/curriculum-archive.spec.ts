import { test, expect } from "./fixtures";
const note = { id: "notes-design", perspective: "exam-notes", title: "三维造型 · 2026-10-09 · 复习笔记", created_at: 1791500000 };
const record = { id: "2044986", course: "design", name: "三维造型与工业设计", title: "2026-10-09第6-7节", date: "2026-10-09", first: 6, last: 7, teacher: "张哲硕", room: "城市学院文3-115", source_url: "https://classroom.hzcu.edu.cn/livingroom?course_id=53450&sub_id=2044986", state: "ready", detail: "", warnings: [], documents: [note] };
const markdown = "# 草图与零件建模\n\n## 课堂正文 [00:02:11]\n\n用装配体组织零件，保留设计意图。\n\n\\[a^2+b^2=c^2\\]\n\n```python\nprint(1 + 2)\n```\n\n## 补充理解\n\n这里是明确标注的补充解释。";
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-10T00:00:00Z"));
  await page.addInitScript(() => localStorage.setItem("openworker.lang", "zh"));
  await page.route("**/v1/curriculum/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/archive")) return route.fulfill({ json: { courses: [{ key: "design", name: record.name }], recordings: [record], coverage: { design: { complete: true, from: "2026-09-14", through: "2026-10-10" } } } });
    if (url.pathname.endsWith("/occurrence")) return route.fulfill({ json: url.searchParams.get("course") === "tourism" ? { state: "excluded", recordings: [] } : url.searchParams.get("course") === "design" && url.searchParams.get("date") === "2026-10-09" && url.searchParams.get("first") === "6" ? { state: "matched", recordings: [record] } : { state: "not_listed", recordings: [] } });
    if (url.pathname.endsWith("/download")) return route.fulfill({ contentType: "text/markdown", body: markdown });
    if (url.pathname.includes("/documents/")) return route.fulfill({ json: { ...note, recording_id: record.id, markdown } });
    return route.fulfill({ status: 404 });
  });
  await page.goto("/");
});
test("reads course groups, downloads notes, and restores the reading link on reload", async ({ page }) => {
  const sources: string[] = [];
  page.on("request", r => { if (/transcript|\/sources\//.test(r.url())) sources.push(r.url()); });
  await page.getByTestId("nav-course-archive").click();
  const view = page.getByRole("main", { name: "课程档案", exact: true });
  await expect(view).toContainText("已整理 1 / 1 次课堂");
  await view.getByRole("button", { name: /2026-10-09第6-7节/ }).click();
  await expect(page.getByRole("article", { name: "课堂笔记正文" })).toContainText("补充理解");
  await expect(page.locator("article .katex math")).toHaveCount(1);
  await expect(page.locator("article pre")).toContainText("print(1 + 2)");
  await page.screenshot({ path: "test-results/course-archive-reader.png" });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 Markdown" }).click();
  expect((await download).suggestedFilename()).toMatch(/复习笔记\.md$/);
  await page.reload();
  await expect(page.getByRole("article", { name: "课堂笔记正文" })).toContainText("装配体组织零件");
  expect(sources).toEqual([]);
});
test("details link only this lecture, while its lab explains the absent platform entry", async ({ page }) => {
  await page.getByTestId("nav-curriculum").click();
  await page.getByLabel("跳转日期").fill("2026-10-09");
  const friday = page.getByRole("region", { name: "2026-10-09" });
  await friday.getByRole("button", { name: /三维造型/ }).last().click();
  await expect(page.getByRole("dialog")).toContainText("平台未列出对应录播");
  await page.keyboard.press("Escape");
  await friday.getByRole("button", { name: /三维造型/ }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: /复习笔记/ }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("article", { name: "课堂笔记正文" })).toContainText("设计意图");
});
test("supports narrow layout and handles an archive service failure", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 850 });
  await page.getByTestId("nav-course-archive").click();
  await page.getByLabel("课程", { exact: true }).selectOption("design");
  await expect(page.getByRole("heading", { name: "三维造型与工业设计" })).toBeVisible();
  await page.screenshot({ path: "test-results/course-archive-narrow.png" });
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ status: 503 }));
  await page.getByRole("button", { name: "刷新档案" }).click();
  await expect(page.getByRole("alert")).toContainText("暂时无法读取或整理");
});

test("excluded tourism details explain the archive scope", async ({ page }) => {
  await page.getByTestId("nav-curriculum").click();
  await page.getByLabel("跳转日期").fill("2026-10-08");
  await page.getByRole("region", { name: "2026-10-08" }).getByRole("button", { name: /可持续旅游/ }).click();
  await expect(page.getByRole("dialog")).toContainText("本次归档已按你的要求排除此课程");
  await expect(page.getByRole("dialog").getByRole("button", { name: /复习笔记/ })).toHaveCount(0);
});

// Actual layout from the archived engineering mathematics lecture.
test("keeps indented equations inside lists and the following lesson readable", async ({ page }) => {
  const lesson = String.raw`1. **数乘结论**
   若 \(A\) 是 \(n\) 阶方阵，则

   \[
   |kA|=k^n|A|,
   \]

   而不是 \(k|A|\)。

## 后续正文

这段讲解必须保持普通正文。`;
  await page.route("**/v1/curriculum/documents/notes-design", route => route.fulfill({ json: { ...note, recording_id: record.id, markdown: lesson } }));
  await page.getByTestId("nav-course-archive").click();
  await page.getByRole("button", { name: /2026-10-09第6-7节/ }).click();
  await expect(page.locator("article .katex-error")).toHaveCount(0);
  await expect(page.locator("article .katex math")).toHaveCount(4);
  await expect(page.getByRole("heading", { name: "后续正文" })).toBeVisible();
});
