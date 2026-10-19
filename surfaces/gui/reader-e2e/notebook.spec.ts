import {test, expect, type Page, type APIRequestContext} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {randomUUID, createHash} from "node:crypto";
import path from "node:path";
const headers = {"X-OpenWorker-Token":"isolated-reader-e2e-token"};
const endpoint = (id:string) => `/api/v1/reader/books/${id}/notebook`;
const state = async (request: APIRequestContext, id:string) => (await request.get(endpoint(id),{headers})).json();
test.beforeEach(async ({context,page}) => {
  await context.addInitScript(() => {
    if (window !== window.top) return;
    (window as any).__COWORKER_HTTP__ = location.origin + "/api";
    (window as any).__COWORKER_WS__ = location.origin.replace("http","ws") + "/api";
    (window as any).__COWORKER_API_TOKEN__ = "isolated-reader-e2e-token";
    localStorage.setItem("openworker.lang","zh");
  });
  await page.setViewportSize({width:1440,height:1000});
});
async function openBook(page:Page, request:APIRequestContext, fixture=path.resolve("../../tests/fixtures/reader/epub3.epub"), tocIndex=0) {
  await page.goto("/?module=reader");
  const bytes = Buffer.concat([await readFile(fixture),Buffer.from(randomUUID())]);
  const imported = page.waitForResponse(r => r.url().endsWith("/v1/reader/books") && r.request().method() === "POST");
  await page.getByLabel("导入 EPUB",{exact:true}).setInputFiles({name:"notebook.epub",mimeType:"application/epub+zip",buffer:bytes});
  expect((await imported).ok()).toBe(true);
  const books = (await (await request.get("/api/v1/reader/books",{headers})).json()).books;
  const id = books.find((b:any) => b.sha256 === createHash("sha256").update(bytes).digest("hex")).id;
  await page.locator(`[data-book-id="${id}"]`).getByRole("button").click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await page.locator(".reader-reading nav button").nth(tocIndex).click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  return id;
}
// Real pointer selection inside the book iframe, including nested tags and emoji.
async function selectText(page:Page, skip = 0) {
  const points = await page.evaluate(skip => {
    const v = document.querySelector("foliate-view") as any;
    const doc = v.renderer.getContents()[0].doc;
    const el = doc.querySelector("#target");
    const frame = doc.defaultView.frameElement.getBoundingClientRect();
    const start = doc.createRange(); start.setStart(el.firstChild,skip); start.setEnd(el.firstChild,skip+1);
    const end = doc.createRange(); end.setStart(el.lastChild,el.lastChild.length-2); end.setEnd(el.lastChild,el.lastChild.length-1);
    const a = start.getBoundingClientRect(), b = end.getBoundingClientRect();
    return {a:{x:frame.x+a.left+1,y:frame.y+a.top+a.height/2},b:{x:frame.x+b.right,y:frame.y+b.top+b.height/2}};
  },skip);
  await page.mouse.move(points.a.x,points.a.y); await page.mouse.down();
  await page.mouse.move(points.b.x,points.b.y,{steps:15}); await page.mouse.up();
  await expect(page.getByRole("button",{name:"高亮选中文字"})).toBeEnabled();
}
async function add(page:Page,request:APIRequestContext,id:string,skip=0) {
  const before = await state(request,id);
  await selectText(page,skip); await page.getByRole("button",{name:"高亮选中文字"}).click();
  await expect.poll(async () => (await state(request,id)).revision).toBe(before.revision+1);
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  return state(request,id);
}
async function saveNote(page:Page,text:string) {
  await page.locator("#reader-note").fill(text);
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
}
async function overlayCount(page:Page) {
  return page.evaluate(() => {
    const v = document.querySelector("foliate-view") as any;
    return v.renderer.getContents().reduce((sum:number,c:any) => sum+(c.overlayer?.element.children.length ?? 0),0);
  });
}

test("notebook: native selection, overlapping ranges, notes, reflow, reload, jump and deletion",async ({page,request},info) => {
  const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));
  const id = await openBook(page,request);
  const first = await add(page,request,id);
  expect(first.highlights[0].anchor.quote).toContain("跨标签");
  expect(first.highlights[0].anchor.quote).toContain("😀");
  await page.locator("#reader-note").fill("这段文字让我重新理解阅读。");
  await page.getByRole("button",{name:"← 书库"}).click();
  await expect(page.locator("foliate-view")).toBeVisible();
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  await page.getByLabel("高亮颜色").selectOption("green");
  await expect.poll(async () => (await state(request,id)).highlights[0].color).toBe("green");
  await page.getByRole("button",{name:"整本笔记",exact:true}).click();
  await saveNote(page,"整本书的核心：阅读与理解分开。");
  await page.getByLabel("收起 Notebook").click();
  await page.getByRole("button",{name:"第一章",exact:true}).click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  const both = await add(page,request,id,4);
  expect(both.highlights).toHaveLength(2);
  expect(both.highlights[0].id).toBe(first.highlights[0].id);
  await expect.poll(()=>overlayCount(page)).toBe(2);
  await page.screenshot({path:info.outputPath("notebook-wide.png")});
  await page.getByLabel("字号").selectOption("28");
  await page.setViewportSize({width:980,height:900});
  await page.reload();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(page.locator("[data-highlight-id]")).toHaveCount(2);
  await expect(page.locator("#reader-note")).toHaveValue("整本书的核心：阅读与理解分开。");
  await expect.poll(()=>overlayCount(page)).toBe(2);
  await page.getByLabel("收起 Notebook").click();
  await page.getByRole("button",{name:"注释",exact:true}).click();
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await page.getByRole("button",{name:"回到高亮原文"}).first().click();
  await expect(page.locator(".reader-notebook")).toBeHidden();
  await expect(page.locator("#reader-note")).toHaveValue("这段文字让我重新理解阅读。");
  await expect.poll(()=>page.evaluate(()=> (document.querySelector("foliate-view") as any).lastLocation.section.current)).toBe(0);
  await expect.poll(()=>overlayCount(page)).toBe(2);
  await expect.poll(() => page.evaluate(position => {
    const view = document.querySelector("foliate-view") as any;
    const doc = view.renderer.getContents()[0].doc;
    const range = view.resolveCFI(position.cfi).anchor(doc);
    return range.toString() === position.quote && view.lastLocation.range.comparePoint(range.startContainer,range.startOffset) === 0;
  }, first.highlights[0].anchor)).toBe(true);
  await page.screenshot({path:info.outputPath("highlight-return-narrow.png")});
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await page.screenshot({path:info.outputPath("notebook-narrow.png")});
  const unchanged = await state(request,id); expect(unchanged).toEqual(both);
  await page.getByRole("button",{name:"删除高亮",exact:true}).first().click();
  await page.getByRole("button",{name:"确认删除"}).click();
  await expect.poll(async ()=>(await state(request,id)).highlights.length).toBe(1);
  await expect.poll(()=>overlayCount(page)).toBe(1);
  await page.reload();
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(page.locator("[data-highlight-id]")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("notebook: failed save and lost committed response retain edits and retry exactly once",async ({page,request},info)=>{
  const id=await openBook(page,request);await add(page,request,id);
  let dropped=false;
  await page.route("**/notebook",async route=>{
    if(route.request().method()!=="PUT" || dropped)return route.continue();
    dropped=true;expect((await route.fetch()).status()).toBe(200);await route.abort("failed");
  });
  await page.locator("#reader-note").fill("响应丢失后仍保存一次");
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.locator(".notebook-error")).toContainText("未保存");
  await expect(page.getByRole("button",{name:"放弃编辑"})).toBeDisabled(); // commit outcome is unknown: reload server instead
  const committed=await state(request,id);
  await page.getByTestId("nav-youtube").click();await expect(page.locator("foliate-view")).toBeVisible();
  await expect(page.locator("#reader-note")).toHaveValue("响应丢失后仍保存一次");
  await page.screenshot({path:info.outputPath("notebook-failure.png")});
  await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  expect(await state(request,id)).toEqual(committed);
});

test("notebook: stale tab cannot overwrite, explicit server adoption resolves conflict",async ({page,context,request})=>{
  const id=await openBook(page,request);await add(page,request,id);
  const other=await context.newPage();await other.goto(page.url());
  await other.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(other.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  await page.getByRole("button",{name:"整本笔记",exact:true}).click();await saveNote(page,"来自第一页");
  await other.locator("#reader-note").fill("旧页面编辑");await other.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(other.locator(".notebook-error")).toContainText("另一页面");
  expect((await state(request,id)).book_note).toBe("来自第一页");
  await other.getByRole("button",{name:"采用服务器笔记"}).click();
  await expect(other.locator("#reader-note")).toHaveValue("来自第一页");await other.close();
});

test("notebook: invalid original quote remains visible as unresolved without painting or overwrite",async ({page,request},info)=>{
  const id=await openBook(page,request);const record=await add(page,request,id);
  record.highlights[0].anchor.quote="不匹配的原文";record.highlights[0].note="必须保留的笔记";
  expect((await request.put(endpoint(id),{headers,data:{...record,operation_id:randomUUID()}})).status()).toBe(200);
  const invalid=await state(request,id);await page.reload();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(page.locator("[data-highlight-id]")).toContainText("待修复");
  await expect(page.locator("[data-highlight-id]")).toContainText("必须保留的笔记");
  expect(await overlayCount(page)).toBe(0);
  await page.getByRole("button",{name:"回到高亮原文"}).click();
  expect(await state(request,id)).toEqual(invalid);
  await page.screenshot({path:info.outputPath("notebook-unresolved.png")});
});

test("notebook: offline edit, real unauthorized response, discard and retry preserve the durable record",async ({page,request})=>{
  const id=await openBook(page,request);await add(page,request,id);
  await page.route("**/notebook",route=>route.request().method()==="PUT"?route.abort("failed"):route.continue());
  await page.locator("#reader-note").fill("离线草稿");
  const prompt=page.waitForEvent("dialog");
  const reload=page.evaluate(()=>location.reload()).catch(()=>null);
  const dialog=await prompt;expect(dialog.type()).toBe("beforeunload");await dialog.dismiss();await reload;
  await expect(page.locator("#reader-note")).toHaveValue("离线草稿");
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.locator(".notebook-error")).toContainText("未保存");
  expect((await state(request,id)).revision).toBe(1);
  await page.unroute("**/notebook");await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  expect((await state(request,id)).highlights[0].note).toBe("离线草稿");
  await page.locator("#reader-note").fill("登录失效后的草稿");
  await page.evaluate(()=>{(window as any).__COWORKER_API_TOKEN__="expired-test-token";});
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.locator(".notebook-error")).toContainText("登录已失效");
  expect((await state(request,id)).revision).toBe(2);
  await page.evaluate(()=>{(window as any).__COWORKER_API_TOKEN__="isolated-reader-e2e-token";});
  await page.getByRole("button",{name:"重试保存"}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  await page.locator("#reader-note").fill("放弃这段编辑");
  await page.getByRole("button",{name:"放弃编辑"}).click();
  await expect(page.locator("#reader-note")).toHaveValue("登录失效后的草稿");
  expect((await state(request,id)).revision).toBe(3);
});

test("notebook: selecting the same passage reuses its identity and clicking a highlight opens its note",async ({page,request})=>{
  const id=await openBook(page,request);const first=await add(page,request,id);
  await saveNote(page,"属于这个范围");
  await page.getByLabel("收起 Notebook").click();
  await page.getByRole("button",{name:"第一章",exact:true}).click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await selectText(page);await page.getByRole("button",{name:"高亮选中文字"}).click();
  await expect(page.locator("#reader-note")).toHaveValue("属于这个范围");
  expect((await state(request,id)).highlights.map((h:any)=>h.id)).toEqual([first.highlights[0].id]);
  await page.getByLabel("收起 Notebook").click();
  await page.getByRole("button",{name:"第一章",exact:true}).click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  const point=await page.evaluate(()=>{
    const view=document.querySelector("foliate-view") as any;
    const doc=view.renderer.getContents()[0].doc;
    doc.getSelection().removeAllRanges();
    const range=doc.createRange();range.selectNodeContents(doc.querySelector("#target strong"));
    const rect=range.getBoundingClientRect(),frame=doc.defaultView.frameElement.getBoundingClientRect();
    return {x:rect.x+frame.x+rect.width/2,y:rect.y+frame.y+rect.height/2};
  });
  await page.mouse.click(point.x,point.y);
  await expect(page.locator(".reader-notebook")).toBeVisible();
  await expect(page.locator("#reader-note")).toHaveValue("属于这个范围");
});


for (const profile of ["private", "vertical", "rtl", "font"]) {
  test(`notebook profile: ${profile} paints the verified range after reflow and reload`,async ({page,request},info)=>{
    const fixture=profile === "private" ? process.env.EDISON_READER_TEST_EPUB : process.env.EDISON_READER_LAYOUT_DIR && path.join(process.env.EDISON_READER_LAYOUT_DIR,`${profile}.epub`);
    test.skip(!fixture,"Optional real book and layout fixtures remain local");
    const id=await openBook(page,request,fixture,profile === "private" ? 5 : 0);
    // Use a real DOM selection in the rendered book for layout-specific ranges;
    // the six core cases exercise pointer input independently in every engine.
    await page.evaluate(()=>{
      const v=document.querySelector("foliate-view") as any,doc=v.renderer.getContents()[0].doc;
      const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT);
      for(let node=walker.nextNode();node;node=walker.nextNode()){
        if(node.textContent.length<20 || !v.lastLocation.range.intersectsNode(node))continue;
        const range=doc.createRange();range.setStart(node,0);range.setEnd(node,Math.min(35,node.textContent.length));
        const sel=doc.getSelection();sel.removeAllRanges();sel.addRange(range);return;
      }
      throw new Error("No visible paragraph available for this fixture");
    });
    await page.getByRole("button",{name:"高亮选中文字"}).click();
    await expect.poll(async ()=>(await state(request,id)).highlights.length).toBe(1);
    await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
    await saveNote(page,"此处需要回读");
    const record=await state(request,id);
    await page.getByLabel("字号").selectOption("28");
    await page.reload();await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
    await page.getByRole("button",{name:"Notebook",exact:true}).click();
    await expect(page.locator("[data-highlight-id]")).toHaveCount(1);
    await page.getByRole("button",{name:"回到高亮原文"}).click();
    await expect(page.locator("#reader-note")).toHaveValue("此处需要回读");
    await expect.poll(()=>overlayCount(page)).toBe(1);
    expect(await state(request,id)).toEqual(record);
    expect(await page.evaluate(position=>{
      const v=document.querySelector("foliate-view") as any;
      return v.resolveCFI(position.cfi).anchor(v.renderer.getContents()[0].doc).toString();
    },record.highlights[0].anchor)).toBe(record.highlights[0].anchor.quote);
    await page.screenshot({path:info.outputPath(`${profile}-notebook-local.png`)});
  });
}
