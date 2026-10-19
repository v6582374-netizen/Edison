import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('server content login requires captions and survives closed browser, new workers and host restart', async ({ page, request, context, browserName }) => {
  test.setTimeout(120000);
  const root = await mkdtemp(join(tmpdir(), 'edison-content-'));
  let backend: ChildProcess;
  let url = '';
  async function start() {
    backend = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py', root], {
      cwd: resolve('../..'), env: { ...process.env, PYTHONPATH: `${resolve('../..')}:${resolve('../../src')}` }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let logs = '';
    backend.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error(logs || 'backend timeout')), 20000);
      backend.stdout!.on('data', data => {
        output += data;
        const line = output.split('\n').find(line => line.startsWith('{"port":'));
        if (line) { clearTimeout(timer); done(JSON.parse(line).port); }
      });
      backend.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
    });
    url = `http://127.0.0.1:${port}`;
    await expect.poll(async () => { try { return (await request.get(url + '/v1/health')).status(); } catch { return 0; } }).toBe(200);
  }
  async function stop() {
    if (backend?.exitCode === null) {
      const ended = new Promise<void>(done => backend.once('exit', () => done()));
      backend.kill('SIGTERM'); await ended;
    }
  }
  let now = Math.floor(Date.now()/1000);
  const advance = async (seconds = 20) => {now += seconds; await writeFile(join(root, 'clock'), String(now));};
  const call = async (capability: string, args = {}) => {
    const response = await request.post(url + '/v1/youtube/capability', {headers: {'X-OpenWorker-Token':'content-e2e'}, data: {capability, arguments:args}});
    const value = await response.json(); expect(value.ok, value.error).toBe(true); return value.result;
  };
  let failOpen = true;
  let loseScreen = false;
  let releaseOpen!: () => void;
  const delayedOpen = new Promise<void>(resolve => { releaseOpen = resolve; });
  try {
    await advance(0); await start();
    await page.route('**/v1/youtube/capability', async route => {
      const capability = route.request().postDataJSON().capability;
      if (capability === 'content_session.open' && failOpen) {
        await delayedOpen;
        await route.abort('failed');
        return;
      }
      if (capability === 'content_session.screen' && loseScreen) {
        await route.abort('failed');
        return;
      }
      const response = await route.fetch({url: url + '/v1/youtube/capability', headers:{...route.request().headers(), 'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang','zh'));
    await page.goto('/'); await page.getByTestId('nav-youtube').click();
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog').getByRole('button',{name:'连接 YouTube',exact:true}).click();
    const content=page.getByRole('region',{name:'YouTube 内容会话'});
    const openView = async () => {
      const open = content.getByRole('button', {name:'打开登录画面', exact:true});
      if (await open.isVisible()) await open.click();
      await expect(content.getByAltText('服务器登录页面')).toBeVisible();
    };
    await expect(content.getByRole('status')).toHaveText('字幕能力尚未验证');
    await expect(page.getByRole('region', {name:'Google 官方授权'}).getByText(/Google 官方授权尚未配置/)).toBeVisible();
    await expect(content.getByText('打开登录画面只会启动服务器浏览器，不会替你登录。')).toBeVisible();
    await content.getByRole('button',{name:'打开登录画面', exact:true}).click();
    await expect(content.getByRole('button',{name:'正在打开登录画面…'})).toBeDisabled();
    releaseOpen();
    await expect(content.getByRole('alert')).toContainText('无法连接 Edison 服务器');
    await expect(content.getByRole('alert')).toContainText('SSH 隧道');
    await expect(content.getByAltText('服务器登录页面')).toHaveCount(0);
    failOpen = false;
    await content.getByRole('button',{name:'重试连接', exact:true}).click();
    await expect(content.getByAltText('服务器登录页面')).toBeVisible();
    await expect(content.getByText('请在下方 YouTube 页面右上角点击“登录 / Sign in”，再在 Google 页面输入账号、密码和验证码。')).toBeVisible();
    loseScreen = true;
    await expect(content.getByRole('alert')).toContainText('登录画面连接已中断');
    await expect(content.getByAltText('服务器登录页面')).toHaveCount(0);
    await expect(content.getByRole('application')).toHaveCount(0);
    loseScreen = false;
    await content.getByRole('button',{name:'重试连接', exact:true}).click();
    await expect(content.getByAltText('服务器登录页面')).toBeVisible();
    await expect(content.getByText(/登录画面连接已中断/)).toHaveCount(0);
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://www.youtube.com/watch?v=abcdefghijk');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'正在检查字幕…'})).toHaveCount(0, {timeout:10000});
    await expect(content.getByRole('status')).toHaveText('需要完成 YouTube 登录');
    await openView();
    await content.getByAltText('服务器登录页面').click();
    if (browserName === 'chromium') {
      await context.grantPermissions(['clipboard-read', 'clipboard-write']);
      await page.evaluate(() => navigator.clipboard.writeText('isolated-paste-example'));
      await content.getByRole('application').press('ControlOrMeta+V');
      await expect.poll(async () => { try {return await readFile(join(root, 'browser-input'),'utf8');} catch {return '';} }).toBe('isolated-paste-example');
    }
    await writeFile(join(root,'caption-mode'),'empty');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'正在检查字幕…'})).toHaveCount(0, {timeout:10000});
    await expect(content.getByRole('status')).toHaveText('字幕能力中断');
    await expect(content.getByRole('alert')).toContainText('字幕结果为空');
    await advance(); await writeFile(join(root,'caption-mode'),'mismatch');
    await openView();
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'正在检查字幕…'})).toHaveCount(0, {timeout:10000});
    await expect(content.getByRole('alert')).toContainText('视频身份不匹配');
    await advance(); await writeFile(join(root,'caption-mode'),'good');
    await openView();
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'正在检查字幕…'})).toHaveCount(0, {timeout:10000});
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await expect(content.getByText('账号一致性：用户确认，系统未自动核验。')).toBeVisible();
    // Native snapshot confirmation closes the browser; older controlled fixtures may still expose close.
    const closeBrowser = content.getByRole('button',{name:'关闭服务器浏览器'});
    if (await closeBrowser.isVisible()) await closeBrowser.click();
    await expect(content.getByRole('button',{name:'打开登录画面',exact:true})).toBeVisible();
    await advance(); await content.getByRole('button',{name:'重新实际获取字幕'}).click();
    await expect(content.getByRole('button',{name:'重新实际获取字幕'})).toBeEnabled();
    await stop(); await start(); await advance();
    await content.getByRole('button',{name:'重新实际获取字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    expect((await call('content_session.status')).browser_running).toBe(false);
    // Enter real manual work through Web; the real host loop owns all scheduling.
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture',refresh_token:'fixture'});
    await advance();
    await call('manual.submit',{url:'https://www.youtube.com/watch?v=mnopqrstuvw',request_id:'content-session-manual'});
    await expect.poll(async () => { await advance(3); return (await call('library.list')).assets.some((asset: {video_id:string; manuscript_version:number|null}) => asset.video_id==='mnopqrstuvw' && asset.manuscript_version !== null); },{timeout:30000}).toBe(true);
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    const records=(await readFile(join(root,'caption-requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    expect(records.length).toBeGreaterThanOrEqual(6);
    expect(new Set(records.map(item=>item.pid)).size).toBe(records.length);
    expect(new Set(records.map(item=>item.path)).size).toBe(records.length);
    expect(await readFile(join(root,'content/current.cookies'),'utf8')).toContain('fixture-session');
    await advance(); await writeFile(join(root,'caption-mode'),'network');
    await content.getByRole('button',{name:'重新实际获取字幕'}).click();
    await expect(content.getByRole('alert')).toContainText('网络暂时不可用');
    expect((await call('content_session.status')).browser_running).toBe(false);
    expect(await page.evaluate(()=>JSON.stringify(localStorage))).not.toContain('fixture-session');
    await content.getByRole('button',{name:'打开登录画面',exact:true}).click();
    await expect(content.getByRole('button',{name:'关闭服务器浏览器'})).toBeEnabled();
    await advance(901);
    await expect.poll(async ()=>(await call('content_session.status')).browser_running,{timeout:10000}).toBe(false);
    await page.reload();
  } finally { releaseOpen(); await stop(); await rm(root,{recursive:true,force:true}); }
});
