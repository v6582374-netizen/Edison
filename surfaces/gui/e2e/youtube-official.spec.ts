import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('official handoff verifies subscriptions, reports quota, and refreshes after host restart', async ({ page, request }) => {
  const root = await mkdtemp(join(tmpdir(), 'edison-official-'));
  let backend: ChildProcess;
  let url = '';
  async function start() {
    backend = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-official-server.py', root], {
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
  try {
    await start();
    await page.route('**/v1/youtube/capability', async route => {
      const response = await route.fetch({ url: url + '/v1/youtube/capability', headers: { ...route.request().headers(), 'X-OpenWorker-Token': 'official-e2e' } });
      await route.fulfill({ response });
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await page.goto('/');
    await page.getByTestId('nav-youtube').click();
    await page.getByRole('button', { name: '订阅频道', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '连接 YouTube', exact: true }).click();
    const official = page.getByRole('region', { name: 'Google 官方授权' });
    await expect(official.getByRole('status')).toHaveText('订阅能力尚未验证');
    await official.getByText('首次连接或重新授权', { exact: true }).click();
    await official.getByLabel('官方授权交接文件').setInputFiles({ name: 'handoff.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({client_id:'fixture-client', client_secret:'fixture-secret', refresh_token:'fixture-refresh'})) });
    await expect(official.getByRole('status')).toHaveText('订阅能力已验证');
    await writeFile(join(root, 'quota'), '');
    await official.getByRole('button', { name: '检查订阅能力' }).click();
    await expect(official.getByRole('status')).toHaveText('订阅能力中断');
    await expect(official.getByText(/项目额度已用尽/).first()).toBeVisible();
    await rm(join(root, 'quota'));
    await stop(); await start();
    await official.getByRole('button', { name: '检查订阅能力' }).click();
    await expect(official.getByRole('status')).toHaveText('订阅能力已验证');
    expect((await readFile(join(root, 'requests'), 'utf8')).split('\n').filter(line => line === 'refresh')).toHaveLength(2);
    const snapshot = await request.post(url + '/v1/youtube/capability', { headers: { 'X-OpenWorker-Token': 'official-e2e' }, data: { capability: 'activity.snapshot' } });
    expect((await snapshot.json()).result.drain_paused).toBe(true);
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('fixture-refresh');
  } finally { await stop(); await rm(root, { recursive: true, force: true }); }
});
