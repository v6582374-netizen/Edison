import { test, expect, mockApi } from './fixtures';
import type { APIRequestContext, BrowserContext, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { access, mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Real Web -> real host background loops -> temporary persistent library. Only
// the native browser, caption responses, Google endpoints, resources and time
// are controlled; no test reaches the queue or recovery state directly.
async function harness(request: APIRequestContext) {
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
    await page.route('**/v1/youtube/capability', async route => route.fulfill({response: await route.fetch({url: url + '/v1/youtube/capability', headers: {...route.request().headers(), 'X-OpenWorker-Token': 'content-e2e'}})}));
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await page.goto('/');
    return panel(page);
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
  return {root, advance, resources, lines, exists, start, stop, raw, call, session, asset, captions, starts, manuscript, panel, tab, connect, cleanup, submit, refuse, mode};
}

test('unattended maintenance restores captions without a page, within a budget kept across normal and crash restarts', async ({page, context, request}) => {
  test.setTimeout(240000);
  const host = await harness(request);
  let viewer: Page | undefined;
  try {
    await host.connect(page);
    expect(await host.starts()).toBe(1);
    // Ordinary absence, network and 429 failures wait in the queue; none is a session basis.
    const settled = (count: number) => expect.poll(async () => {
      await host.advance();
      const state = (await host.asset('ordinary001')).preparation_state;
      return (await host.captions('ordinary001')).length === count && !['queued', 'acquiring'].includes(state);
    }, {timeout: 30000}).toBe(true);
    await host.mode('no_captions'); await host.advance(); await host.submit('ordinary001');
    await settled(1);
    await host.mode('network'); await host.advance(3600);
    await settled(2);
    await host.mode('rate_limit'); await host.advance(600);
    await settled(3);
    await host.advance(900);
    expect(await host.starts()).toBe(1);
    expect((await host.session()).maintenance.state).toBe('idle');
    await host.mode('good');
    await host.manuscript('ordinary001');
    expect(await host.starts()).toBe(1);

    // The page is gone: the host notices the refused snapshot and maintains it alone.
    await page.unrouteAll({behavior: 'ignoreErrors'}); await page.close();
    await host.refuse('fixture-session-1');
    await host.advance(16); await host.submit('lost0000001');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(2);
    const first = (await host.lines('browser-starts.jsonl'))[1].at;
    const running = await host.session();
    expect(running.state).toBe('maintaining');
    expect(running.maintenance).toMatchObject({state: 'running', used: 1, limit: 2, deadline_at: first + 180});
    expect(running.idle_closes_at).toBeNull();
    await host.advance(50);
    await host.manuscript('lost0000001');
    expect((await host.captions('lost0000001')).map(item => item.session)).toEqual(['fixture-session-1', 'fixture-session-2']);
    expect((await host.session()).browser_running).toBe(false);

    // A run that has not finished after three minutes is closed without publishing.
    await host.refuse('fixture-session-1', 'fixture-session-2');
    await host.advance(16); await host.submit('late0000001');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(3);
    await host.advance(181);
    await expect.poll(async () => (await host.session()).maintenance.state, {timeout: 10000}).toBe('waiting_budget');
    const waiting = await host.session();
    expect(waiting.browser_running).toBe(false);
    expect(waiting.maintenance).toMatchObject({used: 2, next_at: first + 86400});
    expect(await readFile(join(host.root, 'snapshot-count'), 'utf8')).toBe('2');
    expect((await host.asset('late0000001')).preparation_state).toBe('queued');

    const opened = await host.tab(context);
    viewer = opened.page;
    const content = opened.content;
    await expect(content.getByRole('status')).toHaveText('后台正在维护内容会话');
    const note = content.getByRole('note', {name: '自动会话维护'});
    await expect(note).toContainText('已用完 2 次');
    await expect(note).toContainText(`下一次可执行：${await viewer.evaluate(at => new Date(at * 1000).toLocaleString(), first + 86400)}`);
    await viewer.unrouteAll({behavior: 'ignoreErrors'}); await viewer.close();

    for (const signal of ['SIGTERM', 'SIGKILL'] as const) {
      await host.stop(signal); await host.start();
      await host.advance(3600);
      await new Promise(done => setTimeout(done, 3000));
      expect(await host.starts()).toBe(3);
      expect((await host.session()).maintenance).toMatchObject({state: 'waiting_budget', used: 2, next_at: first + 86400});
      expect((await host.captions('late0000001')).length).toBe(1);
    }
    await host.advance(first + 86400 - Number(await readFile(join(host.root, 'clock'), 'utf8')) + 1);
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(4);
    await host.advance(50);
    await host.manuscript('late0000001');
    expect((await host.captions('late0000001')).map(item => item.session)).toEqual(['fixture-session-2', 'fixture-session-3']);
    expect((await host.call('activity.snapshot')).drain_paused).toBe(true);
  } finally { await host.cleanup(...(viewer ? [viewer] : []), page); }
});

test('response rotation is maintained, explicit verification hands off and the person reopens an unbounded handoff', async ({page, request}) => {
  test.setTimeout(180000);
  const host = await harness(request);
  try {
    let content = await host.connect(page);
    await host.mode('rotate'); await host.advance(); await host.submit('rota0000001');
    await host.manuscript('rota0000001');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(2);
    await host.mode('good'); await host.advance(50);
    await expect.poll(async () => (await host.session()).maintenance.state, {timeout: 10000}).toBe('idle');
    await host.advance(16); await host.submit('next0000001');
    await host.manuscript('next0000001');
    expect((await host.captions('next0000001'))[0].session).toBe('fixture-session-2');

    // The maintained snapshot is refused again: Google now needs the person.
    await host.refuse('fixture-session-2', 'fixture-session-3');
    await host.advance(16); await host.submit('hand0000001');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(3);
    await host.advance(50);
    await expect.poll(async () => { await host.advance(1); return (await host.session()).maintenance.state; }, {timeout: 30000}).toBe('handoff');
    await page.reload(); content = await host.panel(page);
    await expect(content.getByRole('status')).toHaveText('需要完成 YouTube 登录');
    await expect(content.getByRole('note', {name: '自动会话维护'})).toContainText('后台已停止相关自动尝试');
    await expect(content.getByRole('alert')).toContainText('密码或验证码');
    const requested = (await host.captions('hand0000001')).length;
    for (let day = 0; day < 3; day++) { await host.advance(86400); await new Promise(done => setTimeout(done, 2500)); }
    expect(await host.starts()).toBe(3);
    expect((await host.captions('hand0000001')).length).toBe(requested);
    expect((await host.asset('hand0000001')).preparation_state).toBe('queued');

    await content.getByRole('button', {name: '打开登录画面', exact: true}).click();
    await expect(content.getByAltText('服务器登录页面')).toBeVisible();
    await host.advance(240);
    await new Promise(done => setTimeout(done, 2500));
    expect((await host.session()).browser_running).toBe(true);
    await host.advance(661);
    await expect.poll(async () => (await host.session()).browser_running, {timeout: 10000}).toBe(false);
    await expect(content.getByRole('button', {name: '打开登录画面', exact: true})).toBeVisible();
    await content.getByRole('button', {name: '打开登录画面', exact: true}).click();
    await content.getByAltText('服务器登录页面').click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await content.getByRole('button', {name: '完成登录并检查字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await host.manuscript('hand0000001');
    expect((await host.captions('hand0000001')).at(-1).session).toBe('fixture-session-4');
    expect(await host.starts()).toBe(5);
  } finally { await host.cleanup(page); }
});

test('resource waiting is explained, one maintenance serves concurrent waits and disconnect racing its snapshot revives nothing', async ({page, context, request}) => {
  test.setTimeout(180000);
  const host = await harness(request);
  let viewer: Page | undefined;
  try {
    await host.connect(page);
    await host.resources(1024); await host.refuse('fixture-session-1');
    await host.advance(); await host.submit('conc0000001'); await host.submit('conc0000002');
    await expect.poll(async () => { await host.advance(1); return (await host.session()).maintenance.state; }, {timeout: 30000}).toBe('waiting_resources');
    const waiting = (await host.session()).maintenance;
    expect(waiting.reason).toContain('1.25 GiB');
    expect(waiting.next_at).toBeGreaterThan(0);
    const opened = await host.tab(context);
    viewer = opened.page;
    const note = opened.content.getByRole('note', {name: '自动会话维护'});
    await expect(note).toContainText('1.25 GiB');
    await expect(note).toContainText(`下一次可执行：${await viewer.evaluate(at => new Date(at * 1000).toLocaleString(), waiting.next_at)}`);
    expect(await host.starts()).toBe(1);
    expect((await host.captions('conc0000001')).length).toBe(1);
    expect((await host.captions('conc0000002')).length).toBe(0);

    await host.resources(2048); await host.advance(61);
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(2);
    // The person's open takes over the one profile browser instead of launching another.
    await host.call('content_session.open');
    expect(await host.starts()).toBe(2);
    expect((await host.session()).maintenance.state).toBe('due');
    await host.call('content_session.close');

    await writeFile(join(host.root, 'snapshot-hold'), '1');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(3);
    await host.advance(50);
    await expect.poll(() => host.exists('snapshot-held'), {timeout: 30000}).toBe(true);
    const disconnect = host.call('connection.disconnect');
    await new Promise(done => setTimeout(done, 500));
    await rm(join(host.root, 'snapshot-hold'));
    await disconnect;
    const withdrawn = await host.session();
    expect(withdrawn).toMatchObject({state: 'disconnected', snapshot_present: false, browser_running: false});
    expect(await host.exists('content/current.cookies')).toBe(false);
    const requested = (await host.lines('caption-requests.jsonl')).length;
    for (let day = 0; day < 2; day++) { await host.advance(86400); await new Promise(done => setTimeout(done, 2500)); }
    expect(await host.starts()).toBe(3);
    expect((await host.session()).state).toBe('disconnected');
    expect((await host.lines('caption-requests.jsonl')).length).toBe(requested);
    for (const open of [viewer, page]) await open.unrouteAll({behavior: 'ignoreErrors'});
    await host.stop(); await host.start();
    expect((await host.session()).state).toBe('disconnected');
    expect(await host.starts()).toBe(3);
  } finally { await host.cleanup(...(viewer ? [viewer] : []), page); }
});
