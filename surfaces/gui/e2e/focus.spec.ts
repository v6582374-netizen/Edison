import { test, expect, sendAppEvent } from "./fixtures";
import type { Page } from "@playwright/test";
async function open(page: Page, lang = "zh") {
  await page.addInitScript(value => localStorage.setItem("openworker.lang", value), lang);
  await page.goto("/?module=minimalism");
  await expect(page.getByRole("main", {name:"minimalism",exact:true})).toBeVisible();
}
async function add(page: Page, text: string) {
  await page.getByRole("button", {name:"记下想法",exact:true}).click();
  await page.getByLabel("先记下来", {exact:true}).fill(text);
  await page.getByRole("button", {name:"记下",exact:true}).click();
}
async function arrange(page: Page) { await page.getByRole("button", {name:"安排事项",exact:true}).click(); }
async function close(page: Page) { await page.getByRole("button", {name:"收起",exact:true}).click(); }
async function order(page: Page) { return page.locator(".fm-queue-text").allTextContents(); }

test("continuous task, capture, pointer priority, edit, persistence, and quiet host", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.setViewportSize({width:1440,height:950});
  await open(page);
  await expect(page.locator(".fm-task")).toHaveText("先记下一件事。");
  for (const task of ["写完项目方案","阅读方法章节","回复合作邮件"]) await add(page,task);
  await expect(page.locator(".fm-task")).toHaveText("写完项目方案");
  await expect(page.getByRole("button", {name:/开始专注|退出专注|开启下一件/})).toHaveCount(0);
  await expect(page.locator(".fm-symbol, .fm-eyebrow, .fm-spotlight")).toHaveCount(0);
  await expect(page.locator(".sidebar")).toBeInViewport();
  await expect(page.locator(".fm-task")).toHaveCSS("font-style","italic");
  await expect(page.locator(".fm-words")).toHaveCSS("background-color","rgba(0, 0, 0, 0)");
  await arrange(page);
  const handle = await page.getByRole("button", {name:"排序 回复合作邮件",exact:true}).boundingBox();
  const first = await page.locator(".fm-queue li").first().boundingBox();
  await page.mouse.move(handle!.x+handle!.width/2,handle!.y+handle!.height/2);
  await page.mouse.down();
  await page.mouse.move(first!.x+100,first!.y+first!.height/2,{steps:10});
  await expect(page.locator(".fm-queue .is-drop-target")).toContainText("写完项目方案");
  await page.mouse.up();
  expect(await order(page)).toEqual(["回复合作邮件","写完项目方案","阅读方法章节"]);
  await page.getByRole("button", {name:"修改 回复合作邮件",exact:true}).click();
  await page.getByRole("textbox", {name:"修改事项"}).fill("回复合作邮件并确认时间");
  await page.getByRole("button", {name:"保存",exact:true}).click();
  await page.screenshot({path:"../../reports/minimalism-spotlight/planning-desktop.png",fullPage:true});
  await close(page);
  await add(page,"突然想到：整理访谈资料");
  await expect(page.locator(".fm-task")).toHaveText("回复合作邮件并确认时间");
  await expect(page.locator(".fm-panel")).toHaveCount(0);
  await sendAppEvent(page,{type:"automation_run_started",data:{task_id:"task-1",task_title:"Daily AI News",session_id:"run-live-1",workspace:"/tmp/aw",agent:"cowork",trigger:"schedule"}});
  await expect(page.getByTestId("automation-toast")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".fm-task")).toHaveText("回复合作邮件并确认时间");
  await page.screenshot({path:"../../reports/minimalism-spotlight/focus-desktop.png",fullPage:true});
  await page.getByRole("button", {name:"完成这一件",exact:true}).click();
  await expect(page.locator(".fm-task")).toHaveText("写完项目方案");
  await arrange(page);
  expect(await order(page)).toEqual(["写完项目方案","阅读方法章节","突然想到：整理访谈资料"]);
  expect(errors).toEqual([]);
});

test("keyboard sorting, canceled draft, navigation, and empty queue", async ({ page }) => {
  await open(page); await add(page,"写实验设计"); await add(page,"散步");
  await arrange(page);
  await page.getByRole("button", {name:"排序 散步",exact:true}).focus();
  await page.keyboard.press("ArrowUp");
  expect(await order(page)).toEqual(["散步","写实验设计"]);
  await expect(page.locator(".fm-task")).toHaveText("散步");
  await page.getByRole("button", {name:"修改 散步",exact:true}).click();
  await page.getByRole("textbox", {name:"修改事项"}).fill("不要保存这个修改");
  await page.keyboard.press("Escape");
  await expect(page.locator(".fm-panel")).toBeVisible();
  expect(await order(page)).toEqual(["散步","写实验设计"]);
  await close(page);
  await page.getByRole("button", {name:"记下想法",exact:true}).click();
  await page.getByLabel("先记下来", {exact:true}).fill("周末买笔记本");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", {name:"记下想法",exact:true})).toBeFocused();
  await page.getByRole("button", {name:"记下想法",exact:true}).click();
  await expect(page.getByLabel("先记下来", {exact:true})).toHaveValue("周末买笔记本");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("nav-items")).toBeVisible();
  await expect(page.getByTestId("nav-minimalism")).toBeVisible();
  await page.keyboard.press("Control+b");
  await expect(page.locator(".sidebar")).not.toBeInViewport();
  await page.keyboard.press("Control+b");
  await expect(page.locator(".sidebar")).toBeInViewport();
  await arrange(page);
  await page.getByRole("button", {name:"移除 散步",exact:true}).click();
  await page.getByRole("button", {name:"移除 写实验设计",exact:true}).click();
  await close(page);
  await expect(page.locator(".fm-task")).toHaveText("先记下一件事。");
  await expect(page.getByRole("button", {name:"完成这一件",exact:true})).toHaveCount(0);
});

test("mobile touch priority, long typography, dark theme, and reduced motion", async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({reducedMotion:"reduce"});
  await page.addInitScript(() => localStorage.setItem("openwork-theme","dark"));
  await open(page);
  await expect(page.locator(".sidebar")).toBeInViewport();
  await page.keyboard.press("Control+b");
  await add(page,"读完这一页"); await add(page,"收拾书桌");
  await arrange(page);
  const handle = await page.getByRole("button", {name:"排序 收拾书桌",exact:true}).boundingBox();
  const first = await page.locator(".fm-queue li").first().boundingBox();
  const cdp = await page.context().newCDPSession(page);
  const x=handle!.x+handle!.width/2,y=handle!.y+handle!.height/2;
  await cdp.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x,y}]});
  await cdp.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x:first!.x+90,y:first!.y+first!.height/2}]});
  await expect(page.locator(".fm-queue .is-drop-target")).toContainText("读完这一页");
  await cdp.send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]});
  expect(await order(page)).toEqual(["收拾书桌","读完这一页"]);
  await close(page);
  await page.screenshot({path:"../../reports/minimalism-spotlight/focus-mobile-dark.png",fullPage:true});
  await page.getByRole("button",{name:"完成这一件",exact:true}).click();
  await page.getByRole("button",{name:"完成这一件",exact:true}).click();
  const long="整理本周的项目记录，读完剩下的方法章节，然后把需要讨论的问题写下来，并检查下一次会议的资料是否齐全，先把这件事完成再考虑其他事情。";
  await add(page,long);
  await expect(page.locator(".fm-task")).toHaveText(long);
  expect(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await expect(page.locator(".fm-task")).toHaveCSS("animation-name","none");
  await page.screenshot({path:"../../reports/minimalism-spotlight/long-task-mobile-dark.png",fullPage:true});
});

test("legacy storage preserves current task and all notes without a focus mode", async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("edison:focus:v1")) localStorage.setItem("edison:focus:v1",JSON.stringify({tasks:[{id:"a",text:"第一件"},{id:"b",text:"原来正在做的事"},{id:"c",text:"第三件"}],activeId:"b",completed:null}));
  });
  await open(page);
  await expect(page.locator(".fm-task")).toHaveText("原来正在做的事");
  await add(page,"新想到的事");
  await page.reload(); await arrange(page);
  expect(await order(page)).toEqual(["原来正在做的事","第一件","第三件","新想到的事"]);
  await close(page);
  await page.getByRole("button",{name:"完成这一件",exact:true}).click();
  await expect(page.locator(".fm-task")).toHaveText("第一件");
});

test("failed persistence retains subsequent captures and reports the failure", async ({ page }) => {
  await page.addInitScript(() => {
    const original=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){if(key==="edison:focus:v1")throw new DOMException("full","QuotaExceededError");return original.call(this,key,value);};
  });
  await open(page); await add(page,"第一件"); await add(page,"第二件");
  await expect(page.getByRole("alert")).toContainText("未能保存到浏览器");
  await arrange(page);
  expect(await order(page)).toEqual(["第一件","第二件"]);
  await close(page);
  await expect(page.locator(".fm-task")).toHaveText("第一件");
  await page.getByRole("button",{name:"完成这一件",exact:true}).click();
  await expect(page.locator(".fm-task")).toHaveText("第二件");
});

test("long queues use one hidden scroll area; outside dismissal preserves drafts and task order", async ({ page }) => {
  await page.setViewportSize({width:1440,height:900});
  await page.addInitScript(() => {
    const tasks=Array.from({length:19},(_,i)=>({id:`note-${i}`,text:`事项 ${i+1}：整理资料并确认下一步安排`}));
    localStorage.setItem("edison:focus:v1",JSON.stringify({tasks}));
  });
  await open(page); await arrange(page);
  const panel=page.locator(".fm-panel"), list=page.locator(".fm-queue ol");
  await expect(panel).toHaveCSS("overflow-y","hidden");
  await expect(list).toHaveCSS("scrollbar-width","none");
  expect(await list.evaluate(el=>getComputedStyle(el,"::-webkit-scrollbar").display)).toBe("none");
  expect(await list.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
  const box=await list.boundingBox();
  await page.mouse.move(box!.x+box!.width/2,box!.y+box!.height/2);
  await page.mouse.wheel(0,1600);
  await expect.poll(()=>list.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  await page.getByRole("button",{name:"排序 事项 19：整理资料并确认下一步安排",exact:true}).focus();
  await expect(page.getByRole("button",{name:"修改 事项 19：整理资料并确认下一步安排",exact:true})).toBeInViewport();
  expect(await panel.evaluate(el=>el.scrollTop)).toBe(0);
  await page.screenshot({path:"../../reports/minimalism-spotlight/queue-without-scrollbars.png",fullPage:true});
  await page.locator(".focus-module").click({position:{x:30,y:180}});
  await expect(panel).toHaveCount(0);
  await expect(page.locator(".fm-task")).toHaveText("事项 1：整理资料并确认下一步安排");
  await page.getByRole("button",{name:"记下想法",exact:true}).click();
  await page.getByLabel("先记下来",{exact:true}).fill("未提交的想法");
  // Clicking inside must keep the panel open; switching triggers must open the requested panel.
  await page.getByLabel("先记下来",{exact:true}).click();
  await expect(panel).toBeVisible();
  await arrange(page);
  await expect(page.locator(".fm-queue li")).toHaveCount(19);
  await page.locator(".focus-module").click({position:{x:30,y:180}});
  await expect(panel).toHaveCount(0);
  await page.getByRole("button",{name:"记下想法",exact:true}).click();
  await expect(page.getByLabel("先记下来",{exact:true})).toHaveValue("未提交的想法");
  await page.locator(".focus-module").click({position:{x:30,y:180}});
  await expect(panel).toHaveCount(0);
});

test("empty and populated task hover scales visibly; reduced motion stays still", async ({ page }) => {
  await open(page,"en");
  const task=page.locator(".fm-task");
  // Exercise the English empty-state text reported on the deployed site.
  await expect(task).toHaveText("Begin with one thing.");
  for (const populated of [false,true]) {
    if (populated) {
      await page.getByRole("button",{name:"Capture a thought",exact:true}).click();
      await page.getByRole("textbox",{name:"Capture a thought",exact:true}).fill("Read this page");
      await page.getByRole("button",{name:"Add",exact:true}).click();
    }
    await page.mouse.move(0,0);
    await expect(task).toHaveCSS("opacity","0.9");
    await expect(task).toHaveCSS("transform","matrix(1, 0, 0, 1, 0, 0)");
    const before=await task.boundingBox();
    await task.hover();
    await expect(task).toHaveCSS("opacity","1");
    await expect(task).toHaveCSS("transform","matrix(1.025, 0, 0, 1.025, 0, 0)");
    expect((await task.boundingBox())!.width).toBeGreaterThan(before!.width*1.02);
  }
  await page.emulateMedia({reducedMotion:"reduce"});
  await expect(task).toHaveCSS("transform","none");
  await expect(task).toHaveCSS("transition-duration","0.08s");
});

test("entering minimalism preserves the host sidebar preference", async ({page})=>{
  await open(page);
  await expect(page.locator(".sidebar")).toBeInViewport();
  await expect(page.locator(".app")).not.toHaveClass(/nav-collapsed/);
  await page.getByTestId("nav-items").click();
  await page.getByTestId("nav-minimalism").click();
  await expect(page.locator(".sidebar")).toBeInViewport();
  await page.keyboard.press("Control+b");
  await expect(page.locator(".app")).toHaveClass(/nav-collapsed/);
  await page.reload();
  await expect(page.getByRole("main",{name:"minimalism",exact:true})).toBeVisible();
  await expect(page.locator(".app")).toHaveClass(/nav-collapsed/);
  await page.keyboard.press("Control+b");
  await expect(page.locator(".sidebar")).toBeInViewport();
});
