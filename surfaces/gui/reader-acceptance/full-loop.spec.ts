import {test,expect,type Page} from "@playwright/test";
import {readFile,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import path from "node:path";
import {startServer,root,login} from "./server";

const sha=(bytes:Buffer)=>createHash("sha256").update(bytes).digest("hex");
async function signIn(page:Page){
  await page.goto("/login");
  await page.getByLabel("用户名",{exact:true}).fill(login.username);
  await page.getByLabel("密码",{exact:true}).fill(login.password);
  await page.getByRole("button",{name:"登录",exact:true}).click();
  await expect(page.getByTestId("nav-reader")).toBeVisible();
}
async function ready(page:Page){await expect(page.getByRole("button",{name:"下一页",exact:true})).toBeEnabled();}
async function visible(page:Page,position:any){
  await expect.poll(()=>page.evaluate(p=>{
    const v=document.querySelector("foliate-view") as any;if(!v?.lastLocation)return false;
    const resolved=v.resolveCFI(p.cfi),doc=v.renderer.getContents().find((c:any)=>c.index===resolved.index)?.doc;
    if(!doc || v.lastLocation.range.startContainer.ownerDocument!==doc)return false;
    const range=resolved.anchor(doc);
    return range.toString()===p.quote && v.lastLocation.range.comparePoint(range.startContainer,range.startOffset)===0;
  },position)).toBe(true);
}
for(const profile of ["synthetic","private"]){
  test(`HTTPS complete loop: ${profile}, login expiry and durable process restart`,async({page,context,request},info)=>{
    const fixture=profile==="private"?process.env.EDISON_READER_TEST_EPUB:path.join(root,"tests/fixtures/reader/epub3.epub");
    test.skip(!fixture,"Private EPUB is explicitly opt-in and stays local");
    const server=await startServer(request,info.outputPath("server"));
    try{
      await context.addInitScript(()=>{try{localStorage.setItem("openworker.lang","zh");}catch{}});
      await page.setViewportSize({width:1440,height:1000});
      const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));
      const browserTokens:string[]=[];page.on("request",r=>{if(r.headers()["x-openworker-token"])browserTokens.push(r.url());});
      await page.goto("/?module=reader");await expect(page).toHaveURL(/\/login$/);
      expect((await request.get("/v1/reader/books")).status()).toBe(401);
      await signIn(page);
      const cookies=await context.cookies();
      expect(cookies.find(c=>c.name==="edison_session")).toMatchObject({secure:true,httpOnly:true,sameSite:"Strict"});
      await page.getByTestId("nav-reader").click();
      const imported=page.waitForResponse(r=>r.url().endsWith("/v1/reader/books")&&r.request().method()==="POST");
      await page.getByLabel("导入 EPUB",{exact:true}).setInputFiles(fixture!);
      expect((await imported).status()).toBe(201);
      const books=await(await page.request.get("/v1/reader/books")).json();
      expect(books.books).toHaveLength(1);const book=books.books[0];
      const bytes=await readFile(fixture!);expect(book.sha256).toBe(sha(bytes));
      const row=page.locator(`[data-book-id="${book.id}"]`);
      await row.getByRole("combobox").selectOption("later");await expect(row.getByRole("combobox")).toBeEnabled();
      await row.getByRole("button").click();await ready(page);
      if(profile==="private"){
        await page.locator(".reader-reading nav button").nth(5).click();
        await expect.poll(()=>page.evaluate(()=>(document.querySelector("foliate-view") as any).renderer.getContents()[0].doc.body.textContent.length)).toBeGreaterThan(100);
      }
      // Select real rendered paragraph text; no test creates anchors through the API.
      const quote=await page.evaluate(()=>{
        const v=document.querySelector("foliate-view") as any;
        const doc=v.renderer.getContents()[0].doc;
        const element=[...doc.querySelectorAll("p")].find((e:any)=>e.textContent.trim().length>=20&&e.textContent.length<1200&&e.getClientRects().length) as Element;
        if(!element)throw new Error("Fixture requires a selectable paragraph");
        const range=doc.createRange();range.selectNodeContents(element);
        doc.getSelection().removeAllRanges();doc.getSelection().addRange(range);return range.toString();
      });
      await page.getByRole("button",{name:"高亮选中文字"}).click();
      await expect(page.locator("[data-highlight-id]")).toHaveCount(1);
      await page.locator("#reader-note").fill("整体验收：这条理解属于这段原文。😀");
      await page.getByRole("button",{name:"保存笔记",exact:true}).click();
      await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
      await page.getByRole("button",{name:"整本笔记",exact:true}).click();
      await page.locator("#reader-note").fill("整体验收：整本笔记与摘录分别保存。");
      await page.getByRole("button",{name:"保存笔记",exact:true}).click();
      await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
      const notebookURL=`/v1/reader/books/${book.id}/notebook`,readingURL=`/v1/reader/books/${book.id}/reading`;
      const saved=await(await page.request.get(notebookURL)).json();expect(saved.highlights[0].anchor.quote).toBe(quote);
      await page.getByRole("button",{name:"Notebook",exact:true}).click();
      await page.getByRole("button",{name:"下一页",exact:true}).click();
      await page.getByLabel("字号").selectOption("24");
      await expect(page.getByTestId("reader-save-status")).toHaveText("已保存到服务器");
      const reading=await(await page.request.get(readingURL)).json();expect(reading.position).not.toBeNull();
      const bookURL=page.url();
      // Actual process death, then normal restart against the same data directory.
      await server.restart();
      await page.reload();await expect(page).toHaveURL(/\/login$/);
      await signIn(page);await page.goto(bookURL);await ready(page);
      await expect(page.getByLabel("字号")).toHaveValue("24");await visible(page,reading.position);
      expect(await(await page.request.get(notebookURL)).json()).toEqual(saved);
      expect((await(await page.request.get(readingURL)).json()).position).toEqual(reading.position);
      await page.getByRole("button",{name:"Notebook",exact:true}).click();
      await page.locator("[data-highlight-id] button").first().click();await visible(page,saved.highlights[0].anchor);
      await page.getByRole("button",{name:"书内查找",exact:true}).click();
      await page.getByLabel("查找正文",{exact:true}).fill(quote.trim().slice(0,12));
      await page.getByRole("button",{name:"查找",exact:true}).click();
      await expect(page.locator("[data-search-cfi]").first()).toBeVisible();
      await page.locator("[data-search-cfi]").first().click();
      await expect(page.locator(".reader-text-search [role=alert]")).toHaveCount(0);
      expect(await(await page.request.get(notebookURL)).json()).toEqual(saved);
      await page.getByLabel(`《${book.title}》的归类`).selectOption("archive");
      await expect(page.getByLabel(`《${book.title}》的归类`)).toBeEnabled();
      await page.getByRole("button",{name:"← 书库"}).click();
      await page.getByLabel("查找全库摘录与笔记",{exact:true}).fill("这条理解属于这段原文");
      await page.getByRole("button",{name:"查笔记",exact:true}).click();
      await expect(page.locator(".reader-search-hit")).toHaveCount(1);
      await page.locator(".reader-search-hit").click();await visible(page,saved.highlights[0].anchor);
      await expect(page).not.toHaveURL(/note=/);
      await page.screenshot({path:info.outputPath("complete-loop.png")});
      // Expire the real cookie session while keeping unsaved user text in the reading tab.
      await page.locator("#reader-note").fill("登录恢复后重试，不丢失当前编辑。");
      await page.request.post("/v1/web-auth/logout",{maxRedirects:0});
      for(const endpoint of ["/v1/reader/books",notebookURL,readingURL,`/v1/reader/books/${book.id}/original`,`/v1/reader/books/${book.id}/export?format=json`,`/v1/reader/books/${book.id}/export?format=markdown`,"/v1/reader/search?q=验收"]){
        expect((await page.request.get(endpoint)).status()).toBe(401);
      }
      await page.getByRole("button",{name:"保存笔记",exact:true}).click();
      await expect(page.locator(".reader-notebook [role=alert]")).toBeVisible();
      await expect(page.locator("#reader-note")).toHaveValue("登录恢复后重试，不丢失当前编辑。");
      const loginPage=await context.newPage();await signIn(loginPage);await loginPage.close();
      await page.locator(".reader-notebook").getByRole("button",{name:"重试保存",exact:true}).click();
      await expect(page.getByTestId("notebook-status")).toHaveText("Notebook 已与服务器同步");
      const finalNotes=await(await page.request.get(notebookURL)).json();
      expect(finalNotes.highlights).toHaveLength(1);expect(finalNotes.highlights[0].id).toBe(saved.highlights[0].id);
      expect(finalNotes.highlights[0].note).toBe("登录恢复后重试，不丢失当前编辑。");
      const outputs:any={};
      for(const format of ["JSON","Markdown","原书"]){
        const received=page.waitForEvent("download");
        await page.getByRole("button",{name:format==="原书"?"下载原书":`导出 ${format}`,exact:true}).click();
        const download=await received;const data=await readFile((await download.path())!);outputs[format]=data;
        if(format!=="原书")await download.saveAs(info.outputPath(format==="JSON"?"notes.json":"notes.md"));
      }
      const exported=JSON.parse(outputs.JSON.toString());expect(exported.notebook).toEqual(finalNotes);
      expect(exported.book.id).toBe(book.id);expect(exported.book.location).toBe("archive");
      expect(sha(outputs.原书)).toBe(sha(bytes));expect(outputs.Markdown.toString()).toContain(finalNotes.highlights[0].id);
      expect(browserTokens).toEqual([]);expect(errors).toEqual([]);
      await writeFile(info.outputPath("acceptance.json"),JSON.stringify({profile,book_id:book.id,sha256:book.sha256,content_version:book.content_version,highlights:finalNotes.highlights.length,revision:finalNotes.revision,cookie_login:true,injected_browser_tokens:browserTokens.length,process_restart:true,original_hash_matches:true},null,2));
    }finally{await server.close();}
  });
}
