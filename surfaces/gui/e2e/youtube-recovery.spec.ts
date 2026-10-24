import { test, expect } from './fixtures';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { mkdtemp, mkdir, readdir, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { Page, APIRequestContext } from '@playwright/test';

// Real Web, real host queue and a durable temporary library. Only Google,
// YouTube, the model and time are controlled; credentials are fixture strings.
const SECRETS = ['fixture-secret', 'fixture-refresh', 'fixture-session'];
const python = resolve('../../.venv/bin/python');
const env = {...process.env, PYTHONPATH: `${resolve('../..')}:${resolve('../../src')}`};

function drill(page: Page, request: APIRequestContext, root: string) {
  let backend: ChildProcess | undefined;
  let url = '';
  let clock = Date.now() / 1000;
  const advance = async (seconds = 20) => { clock += seconds; await writeFile(join(root, 'clock'), String(clock)); };
  const exists = async (name: string) => { try { await stat(join(root, name)); return true; } catch { return false; } };
  async function start(contract?: number) {
    backend = spawn(python, ['surfaces/gui/e2e/youtube-content-server.py', root], {
      cwd: resolve('../..'), stdio: ['ignore', 'pipe', 'pipe'],
      env: {...env, ...(contract === undefined ? {} : {EDISON_E2E_DATA_CONTRACT: String(contract)})},
    });
    let logs = ''; backend.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error(logs || 'backend timeout')), 20000);
      backend!.stdout!.on('data', data => { output += data; const line = output.split('\n').find(line => line.startsWith('{"port":')); if (line) { clearTimeout(timer); done(JSON.parse(line).port); } });
      backend!.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
    });
    url = `http://127.0.0.1:${port}`;
    await expect.poll(async () => { try { return (await request.get(url + '/v1/health')).status(); } catch { return 0; } }).toBe(200);
  }
  async function stop(signal: NodeJS.Signals = 'SIGTERM') { if (backend?.exitCode === null) { const ended = new Promise<void>(done => backend!.once('exit', () => done())); backend.kill(signal); await ended; } }
  const raw = (capability: string, args = {}) => page.evaluate(async ({capability, args}) => (await fetch('/v1/youtube/capability', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({capability, arguments: args})})).json(), {capability, args});
  const call = async (capability: string, args = {}) => { const value = await raw(capability, args); expect(value.ok, value.error).toBe(true); return value.result; };
  const recovery = (...args: string[]) => {
    const result = spawnSync(python, ['-m', 'youtube_strataread.workbench.recovery', ...args], {cwd: resolve('../..'), encoding: 'utf8', env});
    return {code: result.status, output: JSON.parse(result.stdout || '{}')};
  };
  async function open() {
    await page.route('**/v1/youtube/capability', async route => {
      // Page polling keeps running while the backend restarts.
      const response = await route.fetch({url: url + '/v1/youtube/capability', headers: {...route.request().headers(), 'X-OpenWorker-Token': 'content-e2e'}}).catch(() => null);
      if (response) await route.fulfill({response}); else await route.abort('connectionrefused');
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await page.goto('/'); await page.getByTestId('nav-youtube').click();
  }
  async function reload() { await page.reload(); await page.getByTestId('nav-youtube').click(); }
  /** Read every listed document through the reading page, as the person would. */
  async function readThroughWeb() {
    await page.getByRole('button', {name: '阅读文档', exact: true}).click();
    const rows = page.locator('.yp-row');
    await expect(rows.first()).toBeVisible();
    const read: Record<string, {markdown: string; translation: boolean}> = {};
    for (let index = 0; index < await rows.count(); index++) {
      await rows.nth(index).click();
      const info = page.getByRole('complementary', {name: '文档信息'});
      const response = page.waitForResponse(item => item.url().includes('/v1/youtube/capability') && item.request().postDataJSON()?.capability === 'documents.get');
      await info.getByRole('button', {name: '复制正文'}).click();
      const document = await (await response).json();
      const videoId = (await response).request().postDataJSON().arguments.video_id;
      expect(document.ok, document.error).toBe(true);
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(document.result.markdown);
      read[videoId] = {markdown: document.result.markdown, translation: await info.getByRole('button', {name: '查看完整译文'}).isVisible()};
    }
    return read;
  }
  /** One library past migration: kept and post-migration documents, a cancellation, two session rotations. */
  async function populate() {
    await call('connection.import_authorization', {client_id: 'fixture', client_secret: 'fixture-secret', refresh_token: 'fixture-refresh'});
    for (let round = 0; round < 2; round++) {
      await advance();  // Request pacing follows the controlled clock.
      await call('content_session.open'); await call('content_session.click', {x: 1, y: 1});
      await call('content_session.confirm', {video_id: 'abcdefghijk', same_account: true});
    }
    await call('manual.submit', {url: 'https://youtu.be/keptvideo01', request_id: 'kept-document'});
    await expect.poll(async () => { await advance(); return (await call('library.inspect', {video_id: 'keptvideo01'})).manuscript_version; }, {timeout: 20000}).toBe(1);
    await call('library.set_reading_state', {video_id: 'keptvideo01', reading_state: 'read'});
    await call('activity.resume');
    await writeFile(join(root, 'model-unavailable'), '');
    await call('manual.submit', {url: 'https://youtu.be/latestdoc01', request_id: 'latest-document'});
    await call('manual.submit', {url: 'https://youtu.be/cancelled01', request_id: 'cancelled-later'});
    await writeFile(join(root, 'model-hold'), ''); await rm(join(root, 'model-unavailable'));
    await expect.poll(async () => { await advance(); return exists('model-active'); }, {timeout: 20000}).toBe(true);
    const current = (await call('activity.snapshot')).current[0].video_id;
    return {current, queued: current === 'latestdoc01' ? 'cancelled01' : 'latestdoc01'};
  }
  async function holdMigrateAndDrain(current: string) {
    await page.getByText('维护工具', {exact: true}).click();
    await page.getByRole('button', {name: '开始切换暂停', exact: true}).click();
    const region = page.getByRole('region', {name: 'YouTube 切换暂停'});
    await expect(region).toContainText('尚无一致性备份');
    const early = await raw('cutover.backup');
    expect(early.kind).toBe('cutover_hold');
    expect(early.error).toContain('所有写者确认结束');
    await region.getByRole('button', {name: '执行历史迁移', exact: true}).click();
    await expect(region).toContainText('历史迁移已完成');
    // Work admitted before the hold finishes after the migration: a newer document.
    await rm(join(root, 'model-hold'));
    await expect.poll(async () => (await call('library.inspect', {video_id: current})).manuscript_version, {timeout: 20000}).toBe(1);
    await expect.poll(async () => (await call('cutover.status')).drained, {timeout: 20000}).toBe(true);
    return region;
  }
  async function facts(ids: string[]) {
    const snapshot = await call('activity.snapshot');
    return {
      assets: Object.fromEntries(await Promise.all(ids.map(async id => {
        const asset = await call('library.inspect', {video_id: id});
        return [id, {state: asset.preparation_state, reading: asset.reading_state, version: asset.manuscript_version,
          transcript: asset.source_trace.transcript_available, translation: asset.source_trace.translation_available ?? false}];
      }))),
      autoUpdate: !snapshot.drain_paused,
      disconnection: (await call('connection.status')).disconnection,
      generation: (await call('content_session.status')).generation,
    };
  }
  async function noCredentialCopies(directory: string) {
    for (const entry of await readdir(directory, {recursive: true, withFileTypes: true})) {
      if (!entry.isFile()) continue;
      const text = await readFile(join(entry.parentPath, entry.name), 'latin1');
      for (const secret of SECRETS) expect(text, `${entry.name} contains credential material`).not.toContain(secret);
    }
  }
  return {start, stop, raw, call, recovery, open, reload, readThroughWeb, populate, holdMigrateAndDrain, facts, advance, noCredentialCopies};
}

test('consistent backup after writer drain restores through the actual reading entry without credentials', async ({page, request, context}, testInfo) => {
  test.setTimeout(180000);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const root = await mkdtemp(join(tmpdir(), 'edison-recovery-'));
  await writeFile(join(root, 'clock'), String(Date.now() / 1000));
  const d = drill(page, request, root);
  try {
    await d.start(); await d.open();
    const outside = await d.raw('cutover.backup');
    expect(outside.kind).toBe('cutover_hold');
    const {current, queued} = await d.populate();
    const region = await d.holdMigrateAndDrain(current);
    await d.call('activity.cancel', {video_ids: [queued]});
    await expect.poll(async () => (await d.call('cutover.status')).drained, {timeout: 20000}).toBe(true);
    const ids = ['keptvideo01', current, queued];
    const atBackup = await d.facts(ids);
    const documents = await d.readThroughWeb();
    expect(Object.keys(documents).sort()).toEqual(['keptvideo01', current].sort());
    await page.getByRole('button', {name: '处理进度', exact: true}).click();

    await region.getByRole('button', {name: '取得一致性备份并验证恢复', exact: true}).click();
    await expect(region).toContainText('一致性备份已通过恢复验证');
    await expect(region).toContainText('文稿 2 篇 · 版本 2 个 · 字幕 2 份 · 完整译文 2 份');
    await expect(region).toContainText('认证资料单独管理，未进入备份。');
    await page.screenshot({path: testInfo.outputPath('backup-verified.png'), fullPage: true});
    const backup = (await d.call('cutover.status')).backup;
    expect(backup.verified).toBe(true);
    expect(Object.values(backup.checks).every(Boolean)).toBe(true);
    expect(backup.path.startsWith(join(await realpath(root), 'youtube-backups'))).toBe(true);
    expect((await stat(backup.path)).mode & 0o077).toBe(0);
    expect((await readdir(backup.path)).sort()).toEqual(['assets', 'manifest.json', 'workspace.sqlite3']);
    const manifest = JSON.parse(await readFile(join(backup.path, 'manifest.json'), 'utf8'));
    expect(manifest.restore_verification.ok).toBe(true);
    expect(manifest.separately_managed).toEqual(['Google 官方授权凭据', 'YouTube 内容会话 profile 与快照']);
    await d.noCredentialCopies(backup.path);

    // Newer facts after the backup: reading, Auto Update off and disconnection.
    await d.call('library.set_reading_state', {video_id: current, reading_state: 'read'});
    await d.call('activity.drain_pause');
    await d.call('connection.disconnect');
    const generation = (await d.call('content_session.status')).generation;
    await d.stop();

    // Disaster drill: the library is lost; restore never overwrites a library.
    await rename(join(root, 'library'), join(root, 'library-lost'));
    const lost = await readFile(join(root, 'library-lost', 'workspace.sqlite3'));
    const refused = d.recovery('restore', backup.path, join(root, 'library-lost'));
    expect(refused.code).toBe(1);
    expect(refused.output.error).toContain('不会覆盖');
    expect(await readFile(join(root, 'library-lost', 'workspace.sqlite3'))).toEqual(lost);
    const restored = d.recovery('restore', backup.path, join(root, 'library'));
    expect(restored.code).toBe(0);
    expect(restored.output.restored).toBe(true);
    const requests = await readFile(join(root, 'google-requests.jsonl'), 'utf8');

    await d.start(); await d.reload();
    await expect(region).toContainText('已从一致性备份恢复');
    expect(await d.readThroughWeb()).toEqual(documents);
    expect(Object.values(documents).every(item => item.translation)).toBe(true);
    // The restored library is the backup moment, not the newer facts above.
    const after = await d.facts(ids);
    expect(after.assets).toEqual(atBackup.assets);
    expect(after.assets[current].reading).toBe('inbox');
    // Separately managed authentication was not rewound by the knowledge restore.
    expect(after.generation).toBe(generation);
    expect((await d.call('content_session.status')).snapshot_present).toBe(false);
    expect(await readFile(join(root, 'credentials.json'), 'utf8').catch(() => '')).not.toContain('fixture-refresh');
    // A historical authorization flag from the backup is not current capability.
    expect((await d.call('connection.status')).subscription_capability.state).not.toBe('available');
    // Acquisition stays held even though the backup moment had Auto Update on.
    expect(atBackup.autoUpdate).toBe(true);
    expect((await d.raw('manual.submit', {url: 'https://youtu.be/newvideo001', request_id: 'after-restore'})).kind).toBe('cutover_hold');
    await d.advance(600);
    await expect.poll(async () => (await d.call('activity.snapshot')).runtime.worker_alive).toBe(false);
    expect(await readFile(join(root, 'google-requests.jsonl'), 'utf8')).toBe(requests);
    await page.screenshot({path: testInfo.outputPath('restored-reading.png'), fullPage: true});
  } finally {
    await rm(join(root, 'model-hold'), {force: true});
    await page.unrouteAll({behavior: 'wait'}); await d.stop(); await rm(root, {recursive: true, force: true});
  }
});

test('historical rollback refusal and current-program restart keep the latest library', async ({page, request, context}, testInfo) => {
  test.setTimeout(180000);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const root = await mkdtemp(join(tmpdir(), 'edison-rollback-'));
  await writeFile(join(root, 'clock'), String(Date.now() / 1000));
  const d = drill(page, request, root);
  try {
    await d.start(); await d.open();
    const {current, queued} = await d.populate();
    expect((await d.call('content_session.status')).generation).toBe(2);
    await d.holdMigrateAndDrain(current);
    expect((await d.call('cutover.backup')).backup.verified).toBe(true);
    // Latest facts after migration and backup.
    await d.call('library.set_reading_state', {video_id: current, reading_state: 'read'});
    await d.call('activity.cancel', {video_ids: [queued]});
    await d.call('activity.drain_pause');
    await d.call('connection.disconnect');
    const ids = ['keptvideo01', current, queued];
    const latest = await d.facts(ids);
    expect(latest.assets[queued].state).toBe('cancelled');
    expect(latest.autoUpdate).toBe(false);
    const documents = await d.readThroughWeb();
    const requests = await readFile(join(root, 'google-requests.jsonl'), 'utf8');

    // Only a program that declares a contract covering the library may run.
    const library = join(root, 'library');
    const compatible = d.recovery('check-program', resolve('../..'), library);
    expect(compatible.code).toBe(0);
    expect(compatible.output).toMatchObject({compatible: true, program_contract: 2, library_contract: 2});
    expect(compatible.output.note).toContain('不包含后端程序、资料或凭据回退');
    const legacy = join(root, 'program-02d2c84', 'src', 'youtube_strataread', 'workbench');
    await mkdir(legacy, {recursive: true});
    const source = spawnSync('git', ['show', '02d2c84:src/youtube_strataread/workbench/workspace.py'], {cwd: resolve('../..'), encoding: 'utf8'});
    expect(source.status).toBe(0);
    await writeFile(join(legacy, 'workspace.py'), source.stdout);
    const undeclared = d.recovery('check-program', join(root, 'program-02d2c84'), library);
    expect(undeclared.code).toBe(1);
    expect(undeclared.output).toMatchObject({compatible: false, program_contract: null});
    // Refusal retains the running reader on the latest library.
    expect(await d.readThroughWeb()).toEqual(documents);
    expect(await d.facts(ids)).toEqual(latest);
    expect((await d.call('activity.snapshot')).runtime.worker_alive).toBe(false);

    // If an older program is started anyway, it serves reading and stays paused.
    await d.stop();
    await d.start(0); await d.reload();
    const region = page.getByRole('region', {name: 'YouTube 切换暂停'});
    await expect(region).toContainText('程序与资料不兼容 · 只读阅读');
    await expect(region).toContainText('资料库由更新的程序写入');
    await expect(region.getByRole('button', {name: '取得一致性备份并验证恢复'})).toHaveCount(0);
    await page.screenshot({path: testInfo.outputPath('incompatible-read-only.png'), fullPage: true});
    expect((await d.raw('manual.submit', {url: 'https://youtu.be/newvideo001', request_id: 'old-program'})).kind).toBe('cutover_hold');
    expect(await d.readThroughWeb()).toEqual(documents);
    expect(await d.facts(ids)).toEqual(latest);
    await d.advance(600);
    expect((await d.call('activity.snapshot')).runtime.worker_alive).toBe(false);
    await d.stop();
    expect(d.recovery('check-program', resolve('../..'), library).output.library_contract).toBe(2);

    // Restart evidence only: no historical released backend declares this contract.
    // The current program runs on the same latest library in place.
    await d.start(); await d.reload();
    await expect(region).toContainText('服务重启已中止切换');
    expect(await d.readThroughWeb()).toEqual(documents);
    expect(await d.facts(ids)).toEqual(latest);
    expect((await d.raw('manual.submit', {url: 'https://youtu.be/newvideo001', request_id: 'rolled-back'})).kind).toBe('cutover_hold');
    await d.advance(600);
    expect(await readFile(join(root, 'google-requests.jsonl'), 'utf8')).toBe(requests);
    expect(await readFile(join(root, 'credentials.json'), 'utf8').catch(() => '')).not.toContain('fixture-refresh');
  } finally {
    await rm(join(root, 'model-hold'), {force: true});
    await page.unrouteAll({behavior: 'wait'}); await d.stop(); await rm(root, {recursive: true, force: true});
  }
});

test('an incompatible program preserves interrupted task facts after a crash', async ({page, request}) => {
  test.setTimeout(90000);
  const root = await mkdtemp(join(tmpdir(), 'edison-incompatible-'));
  await writeFile(join(root, 'clock'), String(Date.now() / 1000));
  const d = drill(page, request, root);
  try {
    await d.start(); await d.open();
    const {current, queued} = await d.populate();
    const before = await d.facts(['keptvideo01', current, queued]);
    expect(before.assets[current].state).toBe('generating');
    await d.stop('SIGKILL');
    await d.start(0); await d.reload();
    await expect(page.getByRole('region', {name: 'YouTube 切换暂停'})).toContainText('程序与资料不兼容');
    expect(await d.facts(['keptvideo01', current, queued])).toEqual(before);
    expect((await d.call('activity.snapshot')).runtime.worker_alive).toBe(false);
  } finally {
    await rm(join(root, 'model-hold'), {force: true});
    await page.unrouteAll({behavior: 'wait'}); await d.stop(); await rm(root, {recursive: true, force: true});
  }
});
