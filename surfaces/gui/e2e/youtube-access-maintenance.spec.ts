import { test, expect, mockApi } from './fixtures';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Actual HTTPS login, Web, host loops and persistent library. The proxy below
// follows Caddy's real forward-auth contract; only external services/time vary.
test('fixed 24-hour Edison expiry leaves unattended content maintenance and latest permissions intact', async ({browser}) => {
  test.setTimeout(120000);
  const root = await mkdtemp(join(tmpdir(), 'edison-access-maintenance-'));
  const context = await browser.newContext({ignoreHTTPSErrors: true, permissions: ['clipboard-read', 'clipboard-write']});
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  await mockApi(page);
  let backend: ChildProcess | undefined;
  let now = 1792368000;
  const loginAt = now;
  const clock = async (at: number) => { now = at; await writeFile(join(root, 'clock'), String(now)); };
  const advance = (seconds = 3) => clock(now + seconds);
  const exists = async (name: string) => { try { await stat(join(root, name)); return true; } catch { return false; } };
  const lines = async (name: string) => { try { return (await readFile(join(root, name), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); } catch { return []; } };
  try {
    await clock(now);
    const certificate = spawnSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-keyout', join(root, 'key.pem'), '-out', join(root, 'cert.pem'), '-subj', '/CN=localhost'], {encoding: 'utf8'});
    expect(certificate.status, certificate.stderr).toBe(0);
    backend = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py', root, 'web-auth'], {
      cwd: resolve('../..'), stdio: ['ignore', 'pipe', 'pipe'],
      env: {...process.env, PYTHONPATH: `${resolve('../..')}:${resolve('../../src')}`},
    });
    let logs = ''; backend.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error(logs || 'backend timeout')), 20000);
      backend!.stdout!.on('data', data => { output += data; const line = output.split('\n').find(line => line.startsWith('{"port":')); if (line) { clearTimeout(timer); done(JSON.parse(line).port); } });
      backend!.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
    });
    const url = `https://127.0.0.1:${port}`;
    await expect.poll(async () => { try { return (await context.request.get(url + '/login')).status(); } catch { return 0; } }).toBe(200);
    await page.route(url + '/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/login' || path.startsWith('/v1/web-auth/')) { await route.continue(); return; }
      if (path.startsWith('/v1/')) { await route.fallback(); return; }
      // Production Caddy serves the Web bundle itself. This isolated host
      // serves the built files after the same actual forward-auth check.
      const headers = await route.request().allHeaders();
      const access = await context.request.get(url + '/v1/web-auth/check', {headers: {cookie: headers.cookie || '', 'x-forwarded-uri': path}, maxRedirects: 0});
      if (access.status() !== 204) { await route.fulfill({response: access}); return; }
      await route.fulfill({response: await route.fetch({headers: {...headers, 'X-OpenWorker-Token': 'content-e2e'}})});
    });
    await page.route('**/v1/web-auth/**', route => route.continue());
    await page.route('**/v1/youtube/capability', async route => {
      const headers = await route.request().allHeaders();
      const access = await context.request.get(url + '/v1/web-auth/check', {headers: {cookie: headers.cookie || '', 'x-forwarded-uri': '/v1/youtube/capability'}});
      if (access.status() !== 204) { await route.fulfill({response: access}); return; }
      await route.fulfill({response: await route.fetch({headers: {...headers, 'X-OpenWorker-Token': 'content-e2e'}})});
    });
    const call = async (capability: string, args = {}) => {
      const value = await page.evaluate(async ({capability, args}) => (await fetch('/v1/youtube/capability', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({capability, arguments: args})})).json(), {capability, args});
      expect(value.ok, value.error).toBe(true); return value.result;
    };
    const check = () => context.request.get(url + '/v1/web-auth/check', {headers: {'x-forwarded-uri': '/v1/youtube/capability'}});
    const login = async () => {
      await page.goto(url + '/login');
      await page.getByLabel('用户名').fill('owner');
      await page.getByLabel('密码', {exact: true}).fill('isolated-access-secret');
      await page.getByRole('button', {name: '登录', exact: true}).click();
      await expect(page).toHaveURL(url + '/');
      // Playwright routes the first URL of a form redirect chain only; a fresh
      // page request now traverses the isolated static forward-auth proxy.
      await page.goto(url + '/');
      await page.getByTestId('nav-youtube').click();
    };
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await login();
    expect((await check()).status()).toBe(204);
    await call('connection.import_authorization', {client_id: 'fixture', client_secret: 'fixture-secret', refresh_token: 'fixture-refresh'});
    await call('content_session.open'); await call('content_session.click', {x: 1, y: 1});
    await call('content_session.confirm', {video_id: 'abcdefghijk', same_account: true});
    await advance(20);
    await call('manual.submit', {url: 'https://youtu.be/kept0000001', request_id: 'retained-before-expiry'});
    await expect.poll(async () => { await advance(); return (await call('library.inspect', {video_id: 'kept0000001'})).manuscript_version; }, {timeout: 20000}).toBe(1);
    const original = await call('documents.get', {video_id: 'kept0000001'});
    await call('library.set_reading_state', {video_id: 'kept0000001', reading_state: 'read'});
    await writeFile(join(root, 'model-unavailable'), '');
    await call('manual.submit', {url: 'https://youtu.be/cancel00001', request_id: 'cancel-before-expiry'});
    await call('activity.cancel', {video_ids: ['cancel00001']});
    await rm(join(root, 'model-unavailable'));
    expect((await call('activity.snapshot')).drain_paused).toBe(true);

    await clock(loginAt + 86370);
    await writeFile(join(root, 'refused-sessions'), 'fixture-session-1');
    await writeFile(join(root, 'model-hold'), '');
    await call('manual.submit', {url: 'https://youtu.be/expired0001', request_id: 'maintenance-across-expiry'});
    await expect.poll(async () => { await advance(1); return (await lines('browser-starts.jsonl')).length; }, {timeout: 15000}).toBe(2);
    expect((await call('content_session.status')).maintenance.state).toBe('running');
    await clock(loginAt + 86399.999);
    expect((await check()).status()).toBe(204);
    const denied = page.waitForResponse(response => response.url().endsWith('/v1/youtube/capability') && response.status() === 401);
    await clock(loginAt + 86400);
    expect((await check()).status()).toBe(401);
    await denied;
    await expect(page).toHaveURL(url + '/login');
    await expect(page.getByText('退出或到期不影响已许可的 YouTube 后台工作。', {exact: false})).toBeVisible();

    // No protected request while expired: only external response logs establish
    // that maintenance produced a usable new snapshot and reached the model.
    await advance(70);
    await expect.poll(() => exists('model-active'), {timeout: 15000}).toBe(true);
    const requests = (await lines('caption-requests.jsonl')).filter(item => item.video_id === 'expired0001');
    expect(requests.map(item => item.session)).toEqual(['fixture-session-1', 'fixture-session-2']);
    expect(await exists('browser-running')).toBe(false);
    await rm(join(root, 'model-hold'));
    await login();
    await expect.poll(async () => (await call('library.inspect', {video_id: 'expired0001'})).manuscript_version, {timeout: 15000}).toBe(1);
    expect(await call('documents.get', {video_id: 'kept0000001'})).toEqual(original);
    expect((await call('library.inspect', {video_id: 'kept0000001'})).reading_state).toBe('read');
    expect((await call('library.inspect', {video_id: 'cancel00001'})).preparation_state).toBe('cancelled');
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    expect((await call('connection.status')).authorized).toBe(true);
    expect((await call('content_session.status')).generation).toBe(2);
    expect((await lines('caption-requests.jsonl')).some(item => item.video_id === 'cancel00001')).toBe(false);
    await page.getByRole('button', {name: '阅读文档', exact: true}).click();
    const rows = page.locator('.yp-row');
    await expect(rows).toHaveCount(2);
    for (let index = 0; index < 2; index++) {
      await rows.nth(index).click();
      const info = page.getByRole('complementary', {name: '文档信息'});
      await expect(info.getByRole('button', {name: '查看完整译文'})).toBeVisible();
      const copied = page.waitForResponse(response => response.url().endsWith('/v1/youtube/capability') && response.request().postDataJSON()?.capability === 'documents.get');
      await info.getByRole('button', {name: '复制正文'}).click();
      const document = await (await copied).json();
      expect(document.ok).toBe(true);
      await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(document.result.markdown);
      expect(document.result.markdown).toContain('The full original source is retained.');
    }
  } finally {
    await rm(join(root, 'model-hold'), {force: true});
    await page.unrouteAll({behavior: 'ignoreErrors'}).catch(() => {}); await context.close().catch(() => {});
    if (backend?.exitCode === null && backend.signalCode === null) {
      const ended = new Promise<void>(done => backend!.once('exit', () => done())); backend.kill('SIGTERM'); await ended;
    }
    await rm(root, {recursive: true, force: true});
  }
});
