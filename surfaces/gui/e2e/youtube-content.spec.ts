import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('server content login requires captions and survives closed browser, new workers and host restart', async ({ page, request }) => {
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
  try {
    await advance(0); await start();
    await page.route('**/v1/youtube/capability', async route => {
      const response = await route.fetch({url: url + '/v1/youtube/capability', headers:{...route.request().headers(), 'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang','zh'));
    await page.goto('/'); await page.getByTestId('nav-youtube').click();
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog').getByRole('button',{name:'连接 YouTube',exact:true}).click();
    const content=page.getByRole('region',{name:'YouTube 内容会话'});
    await expect(content.getByRole('status')).toHaveText('字幕能力尚未验证');
    await content.getByRole('button',{name:'打开服务器登录'}).click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://www.youtube.com/watch?v=abcdefghijk');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'完成登录并检查字幕'})).toBeEnabled({timeout:10000});
    await expect(content.getByRole('status')).toHaveText('需要完成 YouTube 登录');
    await content.getByAltText('服务器登录页面').click();
    await writeFile(join(root,'caption-mode'),'empty');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'完成登录并检查字幕'})).toBeEnabled({timeout:10000});
    await expect(content.getByRole('status')).toHaveText('字幕能力中断');
    await expect(content.getByRole('alert')).toContainText('字幕结果为空');
    await advance(); await writeFile(join(root,'caption-mode'),'mismatch');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'完成登录并检查字幕'})).toBeEnabled({timeout:10000});
    await expect(content.getByRole('alert')).toContainText('视频身份不匹配');
    await advance(); await writeFile(join(root,'caption-mode'),'good');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('button',{name:'完成登录并检查字幕'})).toBeEnabled({timeout:10000});
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await expect(content.getByText('账号一致性：用户确认，系统未自动核验。')).toBeVisible();
    await content.getByRole('button',{name:'关闭服务器浏览器'}).click();
    await expect(content.getByRole('button',{name:'打开服务器登录'})).toBeVisible();
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
    await content.getByRole('button',{name:'打开服务器登录'}).click();
    await expect(content.getByRole('button',{name:'关闭服务器浏览器'})).toBeEnabled();
    await advance(901);
    await expect.poll(async ()=>(await call('content_session.status')).browser_running,{timeout:10000}).toBe(false);
    await page.reload();
  } finally { await stop(); await rm(root,{recursive:true,force:true}); }
});
