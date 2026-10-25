import { test, expect } from "./fixtures";
const note = { id: "notes-design", perspective: "exam-notes", title: "三维造型 · 2026-10-09 · 复习笔记", created_at: 1791500000 };
const record = { id: "2044986", course: "design", name: "三维造型与工业设计", title: "2026-10-09第6-7节", date: "2026-10-09", first: 6, last: 7, teacher: "张哲硕", room: "城市学院文3-115", source_url: "https://classroom.hzcu.edu.cn/livingroom?course_id=53450&sub_id=2044986", state: "ready", detail: "", warnings: [], documents: [note] };
const markdown = "# 草图与零件建模\n\n## 课堂正文 [00:02:11]\n\n用装配体组织零件，保留设计意图。\n\n\\[a^2+b^2=c^2\\]\n\n```python\nprint(1 + 2)\n```\n\n## 补充理解\n\n这里是明确标注的补充解释。";

test("shows edited transcripts, preserved versions and the independent Mac backup state", async ({ page }) => {
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ json: {
    courses: [{ key: "design", name: record.name }],
    recordings: [{ ...record, documents: [{ ...note, perspective: "transcript" }], history: [{ ...note, id: "old-notes" }] }],
    coverage: {}, automation: { enabled: true, errors: {} }, backup: { state: "pending" },
  } }));
  await page.getByTestId("nav-course-archive").click();
  await expect(page.getByRole("status")).toContainText("Mac 待补齐");
  await page.getByRole("button", { name: /2026-10-09第6-7节.*字幕整理稿/ }).click();
  await expect(page.getByLabel("文档版本")).toBeVisible();
  await expect(page.getByLabel("文档版本").locator("option")).toHaveCount(2);
});

test("distinguishes an expired Edison login from a broken archive", async ({ page }) => {
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ status: 401 }));
  await page.getByTestId("nav-course-archive").click();
  await expect(page.getByRole("alert")).toContainText("登录已失效");
  await expect(page.getByRole("link", { name: "登录 Edison" })).toHaveAttribute("href", "/login");
});

test("groups repeated platform failures in both the alert and status details", async ({ page }) => {
  const denied = "平台拒绝访问，请检查请求条件或权限";
  const preparation = "字幕整理失败，来源保留，将重试";
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ json: {
    courses: [{ key: "design", name: record.name }], recordings: [record], coverage: {},
    automation: {
      enabled: true,
      errors: Object.fromEntries([...Array.from({ length: 11 }, (_, i) => [`course-${i}`, denied]), ["generation:123", preparation]]),
      generation_error: denied,
    },
    backup: { state: "synced" },
  } }));
  await page.getByTestId("nav-course-archive").click();
  const alert = page.getByRole("alert");
  await expect(alert.getByText(denied, { exact: true })).toHaveCount(1);
  await expect(alert.getByText(preparation, { exact: true })).toHaveCount(1);
  await page.locator(".course-archive-status-disclosure summary").click();
  const panel = page.locator(".course-archive-status-panel");
  await expect(panel.getByText(denied, { exact: true })).toHaveCount(1);
  await expect(panel.getByText(preparation, { exact: true })).toHaveCount(1);
  await page.screenshot({ path: "test-results/course-platform-errors.png" });
});

test("keeps an expired-login prompt stable and stops automatic requests until sign-in", async ({ page }) => {
  let requests = 0;
  let expired = false;
  await page.route("**/v1/curriculum/archive", route => {
    requests += 1;
    return !expired ? route.fulfill({ json: {
      courses: [{ key: "design", name: record.name }], recordings: [{ ...record, state: "pending", documents: [] }],
      coverage: {}, automation: { enabled: true },
    } }) : route.fulfill({ status: 401 });
  });
  await page.getByTestId("nav-course-archive").click();
  await expect(page.getByText("自动检查已启用", { exact: true }).first()).toBeVisible();
  expired = true;
  await page.getByRole("button", { name: "刷新档案" }).click();
  await expect(page.getByRole("alert")).toContainText("登录已失效");
  const requestsAtExpiry = requests;
  await page.waitForTimeout(6000);
  await expect(page.getByRole("link", { name: "登录 Edison" })).toBeVisible();
  expect(requests).toBe(requestsAtExpiry);
});
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
  await expect(view).toContainText("1 节可阅读");
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

test("keeps course navigation separate from the global missing-caption queue", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const missing = { ...record, id: "missing-math", course: "math", name: "工程数学", date: "2026-10-08", state: "needs_transcript", documents: [], history: [{ ...note, id: "old-math" }], detail: "课后超过三天仍无平台字幕，需要检查" };
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ json: {
    courses: [{ key: "design", name: record.name }, { key: "math", name: "工程数学" }, { key: "empty", name: "尚无课次" }],
    recordings: [record, missing], coverage: {},
  } }));
  await page.getByTestId("nav-course-archive").click();
  const nav = page.getByRole("navigation", { name: "按课程浏览" });
  await expect(nav).toBeVisible();
  await expect(page.getByLabel("课次列表").getByRole("button", { name: /2026-10-09/ })).toBeVisible();
  await page.getByRole("button", { name: /全库 · 1 节课缺少平台字幕/ }).click();
  await expect(page.getByRole("heading", { name: "待平台字幕" })).toBeVisible();
  await expect(page.getByLabel("课次列表")).toContainText("工程数学");
  await expect(page.getByLabel("课次列表")).not.toContainText("三维造型");
  await page.getByRole("button", { name: "返回所选课程" }).click();
  await expect(page.getByRole("heading", { name: record.name })).toBeVisible();
  await nav.getByRole("button", { name: "工程数学 1" }).click();
  await expect(page.getByLabel("课次列表")).toContainText("课后超过三天");
  await page.getByRole("button", { name: "历史 1" }).click();
  await expect(page.getByRole("article")).toBeVisible();
  await page.getByRole("button", { name: "返回课程档案" }).click();
  await expect(page.getByRole("heading", { name: "工程数学" })).toBeVisible();
  await nav.getByRole("button", { name: "尚无课次 0" }).click();
  await expect(page.getByLabel("课次列表")).toContainText("录播尚未核对");
  await page.screenshot({ path: "test-results/course-workspace-desktop.png" });
});

test("discloses independent timestamps and exposes conflicts without opening details", async ({ page }) => {
  let state = "pending";
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ json: {
    courses: [{ key: "design", name: record.name }], recordings: [record], coverage: {},
    automation: { enabled: true, checked_at: "2026-10-13T01:15:24Z" },
    backup: { state, checked_at: "2026-10-13T01:14:50Z", conflicts: state === "conflict" ? ["edited.md"] : [] },
  } }));
  await page.getByTestId("nav-course-archive").click();
  const details = page.locator(".course-archive-status-disclosure");
  await expect(details).not.toHaveAttribute("open");
  await details.locator("summary").click();
  await expect(page.locator(".course-archive-status-panel")).toContainText("09:15:24");
  await expect(page.locator(".course-archive-status-panel")).toContainText("09:14:50");
  await expect(page.locator(".course-archive-status-panel")).toContainText("恢复联网后会自动补齐");
  await details.locator("summary").press("Escape");
  await expect(details).not.toHaveAttribute("open");
  await expect(details.locator("summary")).toBeFocused();
  state = "synced";
  await page.getByRole("button", { name: "刷新档案" }).click();
  await expect(page.getByRole("status")).toHaveText("Mac 已备份");
  state = "conflict";
  await page.getByRole("button", { name: "刷新档案" }).click();
  await expect(page.getByRole("status")).toHaveText("本地修改需核对");
  await expect(page.getByRole("alert")).toContainText("未覆盖你的修改");
  await details.locator("summary").press("Enter");
  await expect(details).toHaveAttribute("open", "");
  await expect(details).toHaveAttribute("data-motion", "keyboard");
  await page.getByRole("heading", { name: "课程档案", exact: true }).click();
  await expect(details).not.toHaveAttribute("open");
});

test("fits the workspace and disclosure at phone width with reduced motion", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/v1/curriculum/archive", route => route.fulfill({ json: {
    courses: [{ key: "design", name: record.name }], recordings: [record], coverage: {},
    automation: { enabled: true }, backup: { state: "synced" },
  } }));
  await page.getByTestId("nav-course-archive").click();
  await page.keyboard.press("ControlOrMeta+b");
  await expect(page.locator(".app")).toHaveClass(/nav-collapsed/);
  await expect(page.getByRole("navigation", { name: "按课程浏览" })).toBeHidden();
  await expect(page.getByLabel("课程", { exact: true })).toBeVisible();
  await page.locator(".course-archive-status-disclosure summary").click();
  const panel = page.locator(".course-archive-status-panel");
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(await page.locator(".course-archive").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: "test-results/course-workspace-phone.png" });
});
