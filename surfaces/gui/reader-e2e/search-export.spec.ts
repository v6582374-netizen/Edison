import {test,expect,type Page,type APIRequestContext} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {randomUUID,createHash} from "node:crypto";
import path from "node:path";
const headers={"X-OpenWorker-Token":"isolated-reader-e2e-token"};
const notebookURL=(id:string)=>`/api/v1/reader/books/${id}/notebook`;
const state=async(request:APIRequestContext,id:string)=>(await request.get(notebookURL(id),{headers})).json();
test.beforeEach(async({context,page})=>{
  await context.addInitScript(()=>{
    if(window!==window.top)return;
    (window as any).__COWORKER_HTTP__=location.origin+"/api";
    (window as any).__COWORKER_WS__=location.origin.replace("http","ws")+"/api";
    (window as any).__COWORKER_API_TOKEN__="isolated-reader-e2e-token";
    localStorage.setItem("openworker.lang","zh");
  });
  await page.setViewportSize({width:1440,height:1000});
});
async function openBook(page:Page,request:APIRequestContext,fixture=path.resolve("../../tests/fixtures/reader/epub3.epub")){
  const bytes=Buffer.concat([await readFile(fixture),Buffer.from(randomUUID())]);
  await page.goto("/?module=reader");
  const imported=page.waitForResponse(r=>r.url().endsWith("/v1/reader/books")&&r.request().method()==="POST");
  await page.getByLabel("导入 EPUB",{exact:true}).setInputFiles({name:"search.epub",mimeType:"application/epub+zip",buffer:bytes});
  expect((await imported).ok()).toBe(true);
  const book=(await(await request.get("/api/v1/reader/books",{headers})).json()).books.find((b:any)=>b.sha256===createHash("sha256").update(bytes).digest("hex"));
  await page.locator(`[data-book-id="${book.id}"]`).getByRole("button").click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();return book;
}
async function search(page:Page,query:string){
  if(!await page.getByLabel("查找正文",{exact:true}).isVisible())await page.getByRole("button",{name:"书内查找",exact:true}).click();
  await page.getByLabel("查找正文",{exact:true}).fill(query);
  await page.getByRole("button",{name:"查找",exact:true}).click();
  await expect(page.getByTestId("text-search-status")).not.toHaveText("正在查找…");
}
async function selectedRange(page:Page,cfi:string){
  return page.evaluate(cfi=>{
    const v=document.querySelector("foliate-view") as any;
    const resolved=v.resolveCFI(cfi),doc=v.renderer.getContents().find((c:any)=>c.index===resolved.index)?.doc;
    if(!doc || v.lastLocation?.range.startContainer.ownerDocument!==doc)return null;
    const range=resolved.anchor(doc);
    return {quote:range.toString(),visible:v.lastLocation.range.comparePoint(range.startContainer,range.startOffset)===0,index:resolved.index};
  },cfi);
}

test("search: Chinese cross-tag phrases, repeated text and final text-node endpoint map to original ranges",async({page,request},info)=>{
  const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));
  await openBook(page,request);
  await search(page,"中文跨标签 English 😀 e\u0301");
  await expect(page.getByTestId("text-search-status")).toHaveText("找到 1 处");
  const hit=page.locator("[data-search-cfi]").first(),cfi=(await hit.getAttribute("data-search-cfi"))!;
  await hit.click();await expect.poll(()=>selectedRange(page,cfi)).toEqual({quote:"中文跨标签 English 😀 e\u0301",visible:true,index:0});
  await search(page,"相同句子不能混淆。");
  await expect(page.locator("[data-search-cfi]")).toHaveCount(2);
  const second=(await page.locator("[data-search-cfi]").nth(1).getAttribute("data-search-cfi"))!;
  expect(second).not.toBe(await page.locator("[data-search-cfi]").first().getAttribute("data-search-cfi"));
  await page.locator("[data-search-cfi]").nth(1).click();
  await expect.poll(()=>selectedRange(page,second)).toEqual({quote:"相同句子不能混淆。",visible:true,index:0});
  await search(page,"返回正文");
  await expect(page.getByTestId("text-search-status")).toHaveText("找到 1 处");
  const last=(await page.locator("[data-search-cfi]").getAttribute("data-search-cfi"))!;
  await page.locator("[data-search-cfi]").click();
  await expect.poll(()=>selectedRange(page,last)).toEqual({quote:"返回正文",visible:true,index:1});
  await expect(page.locator(".reader-text-search [role=alert]")).toHaveCount(0);
  await page.screenshot({path:info.outputPath("search-endpoint.png")});
  await search(page,"[.*]");await expect(page.getByTestId("text-search-status")).toHaveText("找到 0 处");
  expect(errors).toEqual([]);
});

async function makeNotes(page:Page,request:APIRequestContext,id:string,note:string){
  await page.getByRole("button",{name:"第一章",exact:true}).click();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await page.evaluate(()=>{
    const doc=(document.querySelector("foliate-view") as any).renderer.getContents()[0].doc;
    const range=doc.createRange();range.selectNodeContents(doc.querySelector("#target"));
    doc.getSelection().removeAllRanges();doc.getSelection().addRange(range);
  });
  await page.getByRole("button",{name:"高亮选中文字"}).click();
  await expect.poll(async()=>(await state(request,id)).highlights.length).toBe(1);
  await page.locator("#reader-note").fill(note);
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  return state(request,id);
}
async function findNotes(page:Page,query:string){
  await page.getByLabel("查找全库摘录与笔记",{exact:true}).fill(query);
  await page.getByRole("button",{name:"查笔记",exact:true}).click();
  await expect(page.getByRole("button",{name:"查笔记",exact:true})).toBeEnabled();
}

test("search: saved notes across books include archive, navigate by identity and report deleted hits",async({page,request},info)=>{
  const phrase=`笔记查找-${randomUUID()}`;
  const first=await openBook(page,request);
  const saved=await makeNotes(page,request,first.id,phrase);
  await page.getByLabel(`《${first.title}》的归类`).selectOption("archive");
  await expect(page.getByLabel(`《${first.title}》的归类`)).toBeEnabled();
  await page.getByRole("button",{name:"← 书库"}).click();
  const second=await openBook(page,request);
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(page.locator("#reader-note")).toBeEnabled();
  await page.locator("#reader-note").fill(phrase);
  await page.getByRole("button",{name:"保存笔记",exact:true}).click();
  await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
  await page.getByRole("button",{name:"← 书库"}).click();
  await findNotes(page,phrase);
  await expect(page.locator(".reader-note-results [role=status]")).toHaveText("找到 2 条摘录或笔记");
  await page.screenshot({path:info.outputPath("global-notes.png")});
  await page.locator(".reader-note-results .reader-search-hit").filter({hasText:"整本笔记"}).click();
  await expect(page.locator("#reader-note")).toHaveValue(phrase);
  await expect(page).toHaveURL(new RegExp(`book=${second.id}`));
  await expect(page).not.toHaveURL(/note=/); // a consumed result must not pin later reloads to an old target
  await page.reload();await expect(page.locator("#reader-note")).toHaveValue(phrase);
  await page.getByRole("button",{name:"← 书库"}).click();await findNotes(page,phrase);
  await page.locator(".reader-note-results .reader-search-hit").filter({hasText:"归档"}).click();
  await expect(page.locator("#reader-note")).toHaveValue(phrase);
  await expect.poll(()=>selectedRange(page,saved.highlights[0].anchor.cfi)).toEqual({quote:saved.highlights[0].anchor.quote,visible:true,index:0});
  await expect(page).not.toHaveURL(/note=/);
  await page.getByRole("button",{name:"注释",exact:true}).click();
  await expect.poll(async()=>(await(await request.get(`/api/v1/reader/books/${first.id}/reading`,{headers})).json()).position.index).toBe(1);
  await page.reload();
  await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await expect.poll(()=>page.evaluate(()=>(document.querySelector("foliate-view") as any).lastLocation.section.current)).toBe(1);
  await page.getByRole("button",{name:"← 书库"}).click();await findNotes(page,phrase);
  const current=await state(request,first.id);
  expect((await request.put(notebookURL(first.id),{headers,data:{...current,highlights:[],operation_id:randomUUID()}})).status()).toBe(200);
  await page.locator(".reader-note-results .reader-search-hit").filter({hasText:"归档"}).click();
  await expect(page.locator(".reader-notebook")).toContainText("这条高亮已不存在");
  expect((await state(request,first.id)).highlights).toEqual([]);
});

test("export: JSON ranges roundtrip, Markdown contains saved notes, dirty and unauthorized downloads are explicit",async({page,request},info)=>{
  const book=await openBook(page,request);
  const note="导出笔记 😀 e\u0301\n第二行 <script>literal</script>";
  const saved=await makeNotes(page,request,book.id,note);
  await page.locator("#reader-note").fill("这段尚未保存");
  await expect(page.getByRole("button",{name:"导出 JSON",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"导出 Markdown",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"放弃编辑"}).click();
  const jsonDownload=page.waitForEvent("download");
  await page.getByRole("button",{name:"导出 JSON",exact:true}).click();
  const jsonFile=await jsonDownload, data=JSON.parse(await readFile((await jsonFile.path())!,"utf8"));
  expect(data.format).toBe("edison.reader");expect(data.version).toBe(1);expect(data.notebook).toEqual(saved);
  expect(data.book.content_version).toBe(book.content_version);expect(data.book.sha256).toBe(book.sha256);
  expect(await page.evaluate(anchor=>{
    const v=document.querySelector("foliate-view") as any;
    const resolved=v.resolveCFI(anchor.cfi),doc=v.renderer.getContents().find((c:any)=>c.index===resolved.index).doc;
    const range=resolved.anchor(doc),prefix=doc.createRange();prefix.selectNodeContents(doc.body);prefix.setEnd(range.startContainer,range.startOffset);
    return {quote:range.toString(),offset:prefix.toString().length,length:doc.body.textContent.length};
  },data.notebook.highlights[0].anchor)).toEqual({quote:saved.highlights[0].anchor.quote,offset:saved.highlights[0].anchor.offset,length:saved.highlights[0].anchor.length});
  const mdDownload=page.waitForEvent("download");await page.getByRole("button",{name:"导出 Markdown",exact:true}).click();
  const mdFile=await mdDownload,markdown=await readFile((await mdFile.path())!,"utf8");
  expect(markdown.match(/^### /gm)).toHaveLength(1);expect(markdown).toContain(saved.highlights[0].id);
  expect(markdown).toContain("导出笔记 😀 e\u0301");expect(markdown).toContain("&lt;script&gt;");expect(markdown).not.toContain("<script>");
  await jsonFile.saveAs(info.outputPath("reader-notes.json"));await mdFile.saveAs(info.outputPath("reader-notes.md"));
  const downloads:any[]=[];page.on("download",d=>downloads.push(d));
  await page.evaluate(()=>{(window as any).__COWORKER_API_TOKEN__="expired-test-token";});
  await page.getByRole("button",{name:"导出 JSON",exact:true}).click();
  await expect(page.locator(".reader-notebook")).toContainText("导出失败，未生成文件");expect(downloads).toHaveLength(0);
  await page.evaluate(()=>{(window as any).__COWORKER_API_TOKEN__="isolated-reader-e2e-token";});
  expect(await state(request,book.id)).toEqual(saved);
});

test("search: text results and annotations remain separate after resize, close, requery and refresh",async({page,request},info)=>{
  const book=await openBook(page,request);const saved=await makeNotes(page,request,book.id,"查找不得改动笔记");
  await search(page,"跨标签");await expect(page.locator("[data-search-cfi]")).toHaveCount(1);
  const cfi=(await page.locator("[data-search-cfi]").getAttribute("data-search-cfi"))!;
  await page.locator("[data-search-cfi]").click();
  await page.getByLabel("字号").selectOption("28");
  await page.setViewportSize({width:900,height:900});
  await page.locator("[data-search-cfi]").click();
  await expect(page.getByLabel("查找正文",{exact:true})).toBeHidden();
  await expect.poll(()=>selectedRange(page,cfi)).toEqual({quote:"跨标签",visible:true,index:0});
  await page.screenshot({path:info.outputPath("search-narrow-return.png")});
  expect(await state(request,book.id)).toEqual(saved);
  await search(page,"完全不存在的句子");await expect(page.locator("[data-search-cfi]")).toHaveCount(0);
  await expect(page.getByTestId("text-search-status")).toHaveText("找到 0 处");
  await page.reload();await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Notebook",exact:true}).click();
  await expect(page.locator("[data-highlight-id]")).toHaveCount(1);
  expect(await state(request,book.id)).toEqual(saved);
});


test("search: bounded results, invisible origins and replaced queries never claim false success",async({page,request})=>{
  await openBook(page,request,path.resolve("../../tests/fixtures/reader/search-many.epub"));
  await search(page,"查找边界");
  await expect(page.locator("[data-search-cfi]")).toHaveCount(200);
  await expect(page.getByTestId("text-search-status")).toContainText("前 200 处");
  await page.getByLabel("查找正文",{exact:true}).fill("隐藏测试文字");
  await expect(page.locator("[data-search-cfi]")).toHaveCount(0);
  await page.getByRole("button",{name:"查找",exact:true}).click();
  await expect(page.locator("[data-search-cfi]")).toHaveCount(1);
  await page.locator("[data-search-cfi]").click();
  await expect(page.locator(".reader-text-search [role=alert]")).toContainText("无法核对或显示");
  await search(page,"parent.__epubExecuted");await expect(page.getByTestId("text-search-status")).toHaveText("找到 0 处");
  await page.getByRole("button",{name:"← 书库"}).click();
  await expect(page.getByRole("heading",{name:"Reader",exact:true})).toBeVisible();
});

test("search: slow obsolete library response cannot replace a newer query, and authentication errors are visible",async({page,request})=>{
  const book=await openBook(page,request);const phrase=`延迟检索-${randomUUID()}`;
  await makeNotes(page,request,book.id,phrase);
  await page.getByRole("button",{name:"← 书库"}).click();
  let release!:()=>void,arrived!:()=>void;
  const held=new Promise<void>(resolve=>{release=resolve;}),seen=new Promise<void>(resolve=>{arrived=resolve;});
  await page.route("**/reader/search?**",async route=>{
    if(new URL(route.request().url()).searchParams.get("q")!==phrase)return route.continue();
    const response=await route.fetch();arrived();await held;
    await route.fulfill({response}).catch(()=>{});
  });
  await page.getByLabel("查找全库摘录与笔记",{exact:true}).fill(phrase);
  await page.getByRole("button",{name:"查笔记",exact:true}).click();await seen;
  await findNotes(page,phrase+"不存在");
  await expect(page.locator(".reader-note-results [role=status]")).toHaveText("找到 0 条摘录或笔记");
  release();await page.unrouteAll({behavior:"wait"});
  await expect(page.locator(".reader-note-results [role=status]")).toHaveText("找到 0 条摘录或笔记");
  await page.evaluate(()=>{(window as any).__COWORKER_API_TOKEN__="expired-test-token";});
  await findNotes(page,phrase);
  await expect(page.locator(".reader-library-search [role=alert]")).toBeVisible();
  await expect(page.locator(".reader-note-results")).toHaveCount(0);
});
