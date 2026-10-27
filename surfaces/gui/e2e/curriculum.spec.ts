import { test, expect } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-20T04:00:00Z"));
  await page.addInitScript(() => localStorage.setItem("openworker.lang", "zh"));
  await page.goto("/");
});

test("navigates holidays and make-up weeks without duplicating courses", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  const jump = view.getByLabel("跳转日期");
  await jump.fill("2026-09-25");
  await expect(view.getByRole("region", { name: "2026-09-25" }).getByRole("button")).toHaveCount(0);
  await expect(view).toContainText("中秋节放假");
  await jump.fill("2026-10-01");
  await expect(view.getByRole("heading", { name: "第 3 周", exact: true })).toBeVisible();
  await expect(view.getByRole("region", { name: "2026-10-01" }).getByRole("button")).toHaveCount(0);
  await expect(view).toContainText("国庆节放假");
  await view.getByRole("button", { name: "下一周", exact: true }).click();
  await expect(view.getByRole("heading", { name: "第 4 周", exact: true })).toBeVisible();
  await expect(view.getByRole("region", { name: "2026-10-10" }).getByRole("button")).toHaveCount(4);
  await expect(view.getByRole("region", { name: "2026-10-09" }).getByRole("button")).toHaveCount(4);
  await expect(view.getByRole("region", { name: "2026-10-05" }).getByRole("button")).toHaveCount(0);
  await expect(view).toContainText("上单周周五课程");
  await view.getByRole("button", { name: "上一周", exact: true }).click();
  await expect(view.getByRole("heading", { name: "第 3 周", exact: true })).toBeVisible();
  await jump.fill("2026-10-30");
  await expect(view.getByRole("region", { name: "2026-10-30" }).getByRole("button")).toHaveCount(0);
  await expect(view).toContainText("校运会");
  await jump.fill("2026-11-09");
  await expect(view).toContainText("期中考试，不停课");
  await expect(view.getByRole("region", { name: "2026-11-09" }).getByRole("button")).toHaveCount(3);
  await view.getByRole("button", { name: "本周", exact: true }).click();
  await expect(jump).toHaveValue("2026-09-20");
});

test("opens this week's actual courses from the sidebar, including Sunday's make-up classes", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  await expect(view.getByRole("heading", { name: "第 1 周", exact: true })).toBeVisible();
  await expect(view.getByLabel("跳转日期")).toHaveValue("2026-09-20");
  const sunday = view.getByRole("region", { name: "2026-09-20" });
  await expect(sunday.getByRole("button")).toHaveCount(4);
  await expect(sunday).toContainText("09:50–11:25");
  await expect(sunday).toContainText("理5A-325");
  const tuesday = view.getByRole("region", { name: "2026-09-15" });
  await expect(tuesday.getByRole("button", { name: /单片机应用技术/ })).toHaveCount(2);
  await expect(tuesday.getByRole("button", { name: /GO语言程序设计/ })).toHaveCount(2);
  await expect(tuesday.getByRole("button", { name: /C\+\+程序设计及上机/ })).toHaveCount(2);
  await expect(tuesday).toContainText("文3-115");
  await expect(tuesday).toContainText("理5A-302");
  await expect(tuesday).toContainText("理5A-306");
});

test("courses the source schedules in the same periods stay side by side and clickable", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const tuesday = page.getByRole("main", { name: "Curriculum" }).getByRole("region", { name: "2026-09-15" });
  const go = await tuesday.getByRole("button", { name: /GO语言程序设计/ }).first().boundingBox();
  const microcontroller = await tuesday.getByRole("button", { name: /单片机应用技术/ }).first().boundingBox();
  expect(go!.y).toBe(microcontroller!.y);
  expect(go!.x + go!.width <= microcontroller!.x || microcontroller!.x + microcontroller!.width <= go!.x).toBe(true);
  await tuesday.getByRole("button", { name: /GO语言程序设计/ }).first().click();
  await expect(page.getByRole("dialog", { name: "GO语言程序设计" })).toContainText("尹居鑫");
  await page.keyboard.press("Escape");
  await tuesday.getByRole("button", { name: /单片机应用技术/ }).first().click();
  await expect(page.getByRole("dialog", { name: "单片机应用技术" })).toContainText("谢浩");
});

test("keeps the final Monday's courses and distinguishes missing spring data from an empty week", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  await view.getByLabel("跳转日期").fill("2027-01-11");
  await expect(view.getByRole("heading", { name: "第 18 周", exact: true })).toBeVisible();
  await expect(view.getByRole("region", { name: "2027-01-11" }).getByRole("button")).toHaveCount(4);
  await expect(view).toContainText("个人考试安排未提供");
  await view.getByLabel("跳转日期").fill("2027-01-18");
  await expect(view).toContainText("本周 0 次课程");
  await view.getByLabel("跳转日期").fill("2027-03-01");
  await expect(view.getByRole("heading", { name: "第 1 周", exact: true })).toBeVisible();
  await expect(view).toContainText("春季学期课表尚未提供");
  await expect(view).not.toContainText("无课程安排");
  await expect(view).not.toContainText("本周 0 次课程");
  await expect(view).toContainText("开始上课");
  await view.getByLabel("跳转日期").fill("2027-06-09");
  await expect(view).toContainText("端午节");
  await expect(view).toContainText("调休安排以学校通知为准");
  await view.getByLabel("跳转日期").fill("2027-07-05");
  await expect(view).toContainText("暑假（短学期）开始");
  await expect(view).toContainText("此时段未提供个人课表");
});

test("course details expose the correct teacher and class periods, then restore keyboard focus", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  const monday = view.getByRole("region", { name: "2026-09-14" });
  const course = monday.getByRole("button", { name: /物联网技术与原理/ }).last();
  await course.click();
  const dialog = page.getByRole("dialog", { name: "物联网技术与原理" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("张懿");
  await expect(dialog).toContainText("第 8–9 节");
  await expect(dialog).toContainText("15:20–16:55");
  await expect(dialog.getByRole("list", { name: "逐节上下课时间" })).toContainText("第 8 节15:20–16:05");
  await expect(dialog.getByRole("list", { name: "逐节上下课时间" })).toContainText("课间 5 分钟 · 16:05–16:10");
  await expect(dialog.getByRole("list", { name: "逐节上下课时间" })).toContainText("第 9 节16:10–16:55");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(course).toBeFocused();
  await monday.getByRole("button", { name: /物联网技术与原理/ }).first().click();
  await expect(page.getByRole("dialog")).toContainText("郑腾");
  await expect(page.getByRole("dialog")).toContainText("08:00–09:35");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await view.getByRole("region", { name: "2026-09-17" }).getByRole("button", { name: /可持续旅游与数字创新/ }).click();
  await expect(page.getByRole("dialog")).toContainText("全程外教英文授课");
});

test("shows individual start and end times and a real break inside one continuous course", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  await expect(view.getByRole("heading", { name: "Curriculum", exact: true })).toHaveCSS("font-style", "italic");
  const axis = view.getByLabel("逐节上下课时间", { exact: true });
  await expect(axis).toContainText("上课08:00第 1 节下课08:45");
  await expect(axis).toContainText("上课08:50第 2 节下课09:35");
  await expect(axis).toContainText("上课20:10第 12 节下课20:55");
  const course = view.getByRole("region", { name: "2026-09-14" }).getByRole("button", { name: /物联网技术与原理.*08:00–09:35/ });
  await expect(course).toHaveCount(1);
  await expect(course.getByLabel("08:45–08:50 · 课间 5 分钟")).toContainText("课间");
  await expect(course).toContainText("第 2 节08:50–09:35");
  const firstEnd = await axis.locator("time").nth(1).boundingBox();
  const secondStart = await axis.locator("time").nth(2).boundingBox();
  expect(firstEnd!.y + firstEnd!.height).toBeLessThan(secondStart!.y);
  const evening = view.getByRole("region", { name: "2026-09-17" }).getByRole("button", { name: /可持续旅游与数字创新/ });
  await expect(evening.getByLabel("19:15–19:20 · 课间 5 分钟")).toHaveCount(1);
  await expect(evening.getByLabel("20:05–20:10 · 课间 5 分钟")).toHaveCount(1);
  await expect(evening).toContainText("第 12 节20:10–20:55");
  await page.setViewportSize({ width: 390, height: 844 });
  const scroller = view.getByRole("region", { name: "每周课表" });
  await scroller.evaluate(element => element.scrollLeft = element.scrollWidth);
  await expect(axis.locator("time").first()).toBeInViewport();
});

test("course details show the assessment method and keep personal additions across occurrences and reloads", async ({ page }) => {
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  const view = page.getByRole("main", { name: "Curriculum" });
  await view.getByRole("region", { name: "2026-09-15" }).getByRole("button", { name: /C\+\+程序设计及上机/ }).first().click();
  let dialog = page.getByRole("dialog", { name: "C++程序设计及上机" });
  await expect(dialog).toContainText("考核方式考查");
  await expect(dialog).toContainText("限选 · 3 学分");
  await dialog.getByRole("button", { name: "添加信息" }).click();
  await expect(dialog.getByLabel("名称")).toBeFocused();
  await page.keyboard.type("成绩构成");
  await dialog.getByLabel("内容").fill("平时 40% + 大作业 60%\n第 16 周提交");
  await dialog.getByRole("button", { name: "添加信息" }).click();
  await page.keyboard.press("Escape");
  await page.reload();
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  await view.getByRole("region", { name: "2026-09-15" }).getByRole("button", { name: /C\+\+程序设计及上机/ }).last().click();
  dialog = page.getByRole("dialog", { name: "C++程序设计及上机" });
  await expect(dialog.getByLabel("名称")).toHaveCount(1);
  await expect(dialog.getByLabel("名称")).toHaveValue("成绩构成");
  await expect(dialog.getByLabel("内容")).toHaveValue("平时 40% + 大作业 60%\n第 16 周提交");
  await dialog.getByRole("button", { name: "删除此信息" }).click();
  await page.keyboard.press("Escape");
  await view.getByRole("region", { name: "2026-09-14" }).getByRole("button", { name: /工程数学/ }).click();
  await expect(page.getByRole("dialog")).toContainText("考核方式考试");
  await expect(page.getByRole("dialog")).toContainText("必修 · 4 学分");
  await expect(page.getByRole("dialog").getByLabel("名称")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await view.getByRole("region", { name: "2026-09-15" }).getByRole("button", { name: /C\+\+程序设计及上机/ }).first().click();
  await expect(page.getByRole("dialog").getByLabel("名称")).toHaveCount(0);
});

test("weekly overview remains readable in both themes and a narrow window", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Curriculum", exact: true }).click();
  await expect(page.locator(".curriculum-course")).toHaveCount(23);
  await page.screenshot({ path: testInfo.outputPath("curriculum-light.png") });
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  await page.screenshot({ path: testInfo.outputPath("curriculum-dark.png") });
  await page.setViewportSize({ width: 900, height: 760 });
  const scroller = page.getByRole("region", { name: "每周课表" });
  await scroller.evaluate(element => element.scrollLeft = element.scrollWidth);
  await page.getByRole("region", { name: "2026-09-20" }).getByRole("button", { name: /GO语言程序设计/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("curriculum-narrow.png") });
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByLabel("跳转日期").fill("2027-03-01");
  await expect(page.getByRole("status").filter({ hasText: "春季学期课表尚未提供" })).toBeInViewport();
});

test.describe("school timezone", () => {
  test.use({ timezoneId: "America/Los_Angeles" });

  test("uses Shanghai's Monday even when the computer is still on Sunday, with English controls", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-09-20T16:30:00Z"));
    await page.addInitScript(() => localStorage.setItem("openworker.lang", "en"));
    await page.reload();
    await page.getByRole("button", { name: "Curriculum", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Week 2", exact: true })).toBeVisible();
    await expect(page.getByLabel("Jump to date")).toHaveValue("2026-09-21");
    await expect(page.getByRole("button", { name: "Next week", exact: true })).toBeVisible();
    await expect(page.getByLabel("Class period times", { exact: true })).toContainText("Start08:00Period 1End08:45");
    await page.getByLabel("Jump to date").fill("2027-01-01");
    await expect(page.getByRole("heading", { name: "Week 16", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "2027-01-01" }).getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("main", { name: "Curriculum" })).toContainText("New Year’s Day");
    await page.getByRole("button", { name: "This week", exact: true }).click();
    await expect(page.getByLabel("Jump to date")).toHaveValue("2026-09-21");
  });
});
