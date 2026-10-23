import { expect, mockApi } from './fixtures';
import type { APIRequestContext, BrowserContext, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Real Web -> real host background loops -> temporary persistent library. Only
// the native browser, caption responses, Google endpoints, resources and time
// are controlled; no test reaches the queue or recovery state directly.
export async function harness(request: APIRequestContext) {
  const root = await mkdtemp(join(tmpdir(), 'edison-maintenance-'));
  let backend: ChildProcess | undefined;
  let url = '';
  let now = Math.floor(Date.now()/1000);
  const advance = async (seconds = 3) => { now += seconds; await writeFile(join(root, 'clock'), String(now)); };
  const resources = (available: number) => writeFile(join(root, 'resources.json'), JSON.stringify({available: available*1048576, browser_unreclaimable: 0, browser_swap: 0, constrained: true}));
  const lines = async (name: string) => { try { return (await readFile(join(root, name), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); } catch { return []; } };
  const exists = async (name: string) => { try { await access(join(root, name)); return true; } catch { return false; } };
  async function start() {
    const child = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py', root], {
      cwd: resolve('../..'), env: {...process.env, PYTHONPATH: `${resolve('../..')}:${resolve('../../src')}`}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    backend = child;
    let logs = ''; child.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error(logs || 'backend timeout')), 20000);
      child.stdout!.on('data', data => { output += data; const line = output.split('\n').find(line => line.startsWith('{"port":')); if (line) { clearTimeout(timer); done(JSON.parse(line).port); } });
      child.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
    });
    url = `http://127.0.0.1:${port}`;
    await expect.poll(async () => { try { return (await request.get(url + '/v1/health')).status(); } catch { return 0; } }).toBe(200);
  }
  async function stop(signal: NodeJS.Signals = 'SIGTERM') {
    if (backend?.exitCode === null && backend.signalCode === null) {
      const ended = new Promise<void>(done => backend!.once('exit', () => done()));
      backend.kill(signal); await ended;
    }
  }
  const raw = async (capability: string, args = {}) => (await request.post(url + '/v1/youtube/capability', {headers: {'X-OpenWorker-Token': 'content-e2e'}, data: {capability, arguments: args}, timeout: 90000})).json();
  const call = async (capability: string, args = {}) => { const value = await raw(capability, args); expect(value.ok, value.error).toBe(true); return value.result; };
  const session = () => call('content_session.status');
  const asset = (video: string) => call('library.inspect', {video_id: video});
  const captions = async (video: string) => (await lines('caption-requests.jsonl')).filter(item => item.video_id === video);
  const starts = async () => (await lines('browser-starts.jsonl')).length;
  const manuscript = async (video: string) => expect.poll(async () => { await advance(); return (await asset(video)).manuscript_version; }, {timeout: 30000}).not.toBeNull();
  async function panel(page: Page) {
    await page.getByTestId('nav-youtube').click();
    await page.getByRole('button', {name: '订阅频道', exact: true}).click();
    await page.getByRole('dialog').getByRole('button', {name: '连接 YouTube', exact: true}).click();
    return page.getByRole('region', {name: 'YouTube 内容会话'});
  }
  async function view(page: Page) {
    // A restarting backend aborts the page's poll instead of failing the test.
    await page.route('**/v1/youtube/capability', async route => {
      try { await route.fulfill({response: await route.fetch({url: url + '/v1/youtube/capability', headers: {...route.request().headers(), 'X-OpenWorker-Token': 'content-e2e'}})}); }
      catch { await route.abort().catch(() => {}); }
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await page.goto('/');
    return panel(page);
  }
  async function home(page: Page) {
    await page.goto('/');
    await page.getByTestId('nav-youtube').click();
  }
  async function tab(context: BrowserContext) {
    const page = await context.newPage();
    await mockApi(page);
    return {page, content: await view(page)};
  }
  async function connect(page: Page) {
    await advance(0); await resources(2048); await start();
    const content = await view(page);
    await content.getByRole('button', {name: '打开登录画面', exact: true}).click();
    await content.getByAltText('服务器登录页面').click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await content.getByRole('button', {name: '完成登录并检查字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await call('connection.import_authorization', {client_id: 'fixture', client_secret: 'fixture', refresh_token: 'fixture'});
    return content;
  }
  async function cleanup(...pages: Page[]) {
    for (const page of pages) if (!page.isClosed()) await page.unrouteAll({behavior: 'ignoreErrors'});
    await stop(); await rm(root, {recursive: true, force: true});
  }
  const submit = (video: string) => call('manual.submit', {url: `https://youtu.be/${video}`, request_id: `maintenance-${video}`});
  const refuse = (...sessions: string[]) => writeFile(join(root, 'refused-sessions'), sessions.join('\n'));
  const mode = (value: string) => writeFile(join(root, 'caption-mode'), value);
  const flag = (name: string, on = true) => on ? writeFile(join(root, name), '1') : rm(join(root, name), {force: true});
  return {root, flag, home, advance, resources, lines, exists, start, stop, raw, call, session, asset, captions, starts, manuscript, panel, tab, connect, cleanup, submit, refuse, mode};
}
