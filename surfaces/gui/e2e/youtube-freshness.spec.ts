import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, writeFile, access, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { Page, APIRequestContext } from '@playwright/test';

const NOW = 1_790_000_000;
const HOUR = 3600;
type Video = {id: string; ts: number | null; published_at?: string};

async function harness(page: Page, request: APIRequestContext, videos: Video[], legacy = false, retained = false) {
  const dir = await mkdtemp(join(tmpdir(), 'edison-freshness-'));
  let backend: ChildProcess | undefined;
  let url = '';
  const write = (name: string, text = 'go') => writeFile(join(dir, name), text);
  const exists = async (name: string) => {try {await access(join(dir,name)); return true;} catch {return false;}};
  await write('clock', String(NOW));
  await write('videos.json', JSON.stringify(videos));
  if (legacy) await write('legacy');
  if (retained) await write('retained');
  const start = async () => {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-freshness-server.py', dir], {cwd: resolve('../..'), stdio: ['ignore','pipe','pipe']});
    let logs = '';
    backend.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      const timer = setTimeout(() => reject(new Error(logs || 'startup timeout')), 30000);
      let out = '';
      backend!.stdout!.on('data', data => {
        out += data;
        const line = out.split('\n').find(l => l.startsWith('{"port":'));
        if (line) {clearTimeout(timer); done(JSON.parse(line).port);}
      });
      backend!.once('exit', code => {clearTimeout(timer); reject(new Error(`backend ${code}: ${logs}`));});
    });
    url = `http://127.0.0.1:${port}`;
    await expect.poll(async () => {try {return (await request.get(url + '/v1/health')).status();} catch {return 0;}}).toBe(200);
  };
  const stop = async (crash = false) => {
    if (!backend || backend.exitCode !== null) return;
    const exited = new Promise<void>(done => backend!.once('exit', () => done()));
    backend.kill(crash ? 'SIGKILL' : 'SIGTERM');
    await exited;
  };
  const call = async (capability: string, args = {}) => {
    const response = await request.post(url + '/v1/youtube/capability', {
      headers: {'X-OpenWorker-Token':'freshness-e2e'}, data: {capability, arguments:args},
    });
    const payload = await response.json();
    expect(payload.ok, payload.error).toBe(true);
    return payload.result;
  };
  await start();
  await page.route('**/v1/youtube/capability', async route => {
    try {
      const response = await route.fetch({url: url + '/v1/youtube/capability', headers: {...route.request().headers(), 'X-OpenWorker-Token':'freshness-e2e'}});
      await route.fulfill({response});
    } catch {await route.abort();}
  });
  await page.addInitScript(() => localStorage.setItem('openworker.lang','zh'));
  await page.goto('/');
  await page.getByTestId('nav-youtube').click();
  await page.getByRole('button', {name:'处理进度', exact:true}).click();
  const queue = page.getByRole('region', {name:'处理队列', exact:true});
  const toggle = async (on: boolean) => {
    await page.getByRole('button', {name: on ? '恢复自动更新' : '完成当前文档后暂停', exact:true}).click();
    await expect(page.getByRole('button', {name: on ? '完成当前文档后暂停' : '恢复自动更新', exact:true})).toBeEnabled();
  };
  const scan = () => page.getByRole('button', {name:'立即检查更新', exact:true}).click();
  return {dir, queue, write, exists, start, stop, call, toggle, scan,
    clock: (timestamp: number) => write('clock', String(timestamp)),
    close: async () => {await stop(true); await rm(dir, {recursive:true, force:true});},
  };
}

test('discovery admits the inclusive 72-hour boundary and retains uncertain/expired videos', async ({page, request}) => {
  test.setTimeout(60000);
  const app = await harness(page, request, [
    {id:'inside', ts:NOW - 72 * HOUR + 1}, {id:'boundary', ts:NOW - 72 * HOUR},
    {id:'outside', ts:NOW - 72 * HOUR - 1}, {id:'future', ts:NOW + 1},
    {id:'conflict', ts:NOW, published_at:'2020-01-01T00:00:00Z'}, {id:'missing', ts:null, published_at:''},
  ]);
  try {
    await app.scan();
    await expect(app.queue.locator('li')).toHaveCount(2);
    await expect(app.queue).toContainText('inside');
    await expect(app.queue).toContainText('boundary');
    await app.queue.getByRole('button', {name:/^已过期/}).click();
    await expect(app.queue.locator('li')).toHaveCount(1);
    await expect(app.queue).toContainText('outside');
    await app.queue.getByRole('button', {name:/^待核实时间/}).click();
    await expect(app.queue.locator('li')).toHaveCount(3);
    await app.write('classification-release');
    await app.write('captions-release');
    await app.write('model-release');
    await app.write('model-ready');
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:25000}).toBe(2);
    expect(await app.exists('caption-outside')).toBe(false);
  } finally {await app.close();}
});

for (const beforeClaim of [true, false]) {
  test(`71-hour admission cannot first commence at 73 hours (${beforeClaim ? 'queued' : 'classifying'})`, async ({page, request}) => {
    const app = await harness(page, request, [{id:'crossing', ts:NOW - 71 * HOUR}]);
    try {
      await app.scan();
      await expect(app.queue.locator('li')).toHaveCount(1);
      if (!beforeClaim) {
        await app.write('model-ready');
        await app.toggle(true);
        await expect.poll(() => app.exists('classification-crossing')).toBe(true);
      }
      await app.clock(NOW + 2 * HOUR);
      await app.write('classification-release');
      if (beforeClaim) {await app.write('model-ready'); await app.toggle(true);}
      await expect.poll(async () => (await app.call('activity.snapshot')).expired).toBe(1);
      expect(await app.exists('caption-crossing')).toBe(false);
      expect(await app.exists('model-started')).toBe(false);
      await app.queue.getByRole('button', {name:/^已过期/}).click();
      await expect(app.queue).toContainText('首次开工前已超过 72 小时');
    } finally {await app.close();}
  });
}

for (const crash of [false, true]) for (const on of [false, true]) {
  test(`Auto Update ${on ? 'on' : 'off'} survives ${crash ? 'crash' : 'normal'} restart`, async ({page, request}) => {
    test.setTimeout(60000);
    const app = await harness(page, request, []);
    try {
      expect((await app.call('activity.snapshot')).drain_paused).toBe(true);
      if (on) await app.toggle(true);
      else {await app.toggle(true); await app.toggle(false);}
      await app.stop(crash);
      await app.start();
      expect((await app.call('activity.snapshot')).drain_paused).toBe(!on);
      await expect(page.getByRole('button', {name:on ? '完成当前文档后暂停' : '恢复自动更新', exact:true})).toBeVisible();
    } finally {await app.close();}
  });
}

test('71-hour caption commencement survives a crash at 100 hours; disabled Auto Update delays reclaim', async ({page, request}) => {
  test.setTimeout(90000);
  const app = await harness(page, request, [{id:'continuing', ts:NOW - 71 * HOUR}]);
  try {
    await app.write('model-ready');
    await app.write('classification-release');
    await app.toggle(true);
    await expect.poll(() => app.exists('caption-continuing')).toBe(true);
    await app.toggle(false);
    await app.stop(true);
    await app.clock(NOW + 29 * HOUR);
    await app.write('captions-release');
    await app.write('model-release');
    await app.start();
    await expect(app.queue.locator('li')).toHaveCount(1);
    await expect(app.queue).toContainText('已实际开工，可跨窗口续办');
    expect(await app.exists('model-started')).toBe(false);
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:20000}).toBe(1);
    await page.getByRole('button', {name:'阅读文档', exact:true}).click();
    await page.getByRole('combobox', {name:'按时间筛选'}).selectOption({label:'全部时间'});
    await expect(page.getByRole('button', {name:/continuing/})).toBeVisible();
  } finally {await app.close();}
});

test('cancellation is not revived by discovery or restart', async ({page, request}) => {
  const app = await harness(page, request, [{id:'cancelled-video', ts:NOW}]);
  try {
    await app.scan();
    await expect(app.queue.locator('li')).toHaveCount(1);
    await app.queue.getByRole('button', {name:'取消处理', exact:true}).click();
    await app.scan();
    await app.stop(true);
    await app.start();
    await app.scan();
    await app.queue.getByRole('button', {name:/^已取消/}).click();
    await expect(app.queue).toContainText('cancelled-video');
    expect((await app.call('activity.snapshot')).cancelled).toBe(1);
  } finally {await app.close();}
});

test('legacy evidence matrix is conservative and idempotent; matching captions enter translation directly', async ({page, request}) => {
  test.setTimeout(90000);
  const app = await harness(page, request, [], true);
  try {
    await expect(app.queue.locator('li')).toHaveCount(3);
    await expect(app.queue.locator('li').filter({hasText:'evidence-old'})).toContainText('已实际开工');
    await app.queue.getByRole('button', {name:/^已过期/}).click();
    await expect(app.queue.locator('li')).toHaveCount(2);
    await expect(app.queue).toContainText('no-evidence-old');
    await expect(app.queue).toContainText('mismatched-old');
    await app.stop(true);
    await app.start();
    expect((await app.call('activity.snapshot')).expired).toBe(2);
    await app.write('classification-release');
    await app.write('captions-release');
    await app.write('model-ready');
    await app.toggle(true);
    await expect.poll(() => app.exists('model-started')).toBe(true);
    expect(await app.exists('caption-evidence-old')).toBe(false);
    await app.toggle(false);
    await app.stop(true);
    await app.clock(NOW + 29 * HOUR);
    await app.start();
    await app.toggle(true);
    await app.write('model-release');
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:20000}).toBe(2);
    expect(await app.exists('caption-evidence-old')).toBe(false);
    expect(await app.exists('caption-evidence-fresh')).toBe(false);
    expect(await app.exists('caption-no-evidence-fresh')).toBe(false);
    expect((await app.call('activity.snapshot')).expired).toBe(3);
  } finally {await app.close();}
});


test('metadata probing is not commencement and live timing stays pending', async ({page, request}) => {
  test.setTimeout(60000);
  const app = await harness(page, request, [{id:'probe-video', ts:NOW - 71 * HOUR}, {id:'event-video', ts:NOW}]);
  try {
    await app.write('hold-metadata');
    await app.write('classification-release');
    await app.write('model-ready');
    await app.toggle(true);
    await expect.poll(() => app.exists('metadata-probe-video')).toBe(true);
    await app.clock(NOW + 2 * HOUR);
    await app.write('metadata-release');
    await expect.poll(async () => (await app.call('activity.snapshot')).expired, {timeout:20000}).toBe(1);
    await expect.poll(async () => (await app.call('activity.snapshot')).awaiting_timing, {timeout:20000}).toBe(1);
    expect(await app.exists('caption-probe-video')).toBe(false);
    expect(await app.exists('caption-event-video')).toBe(false);
    await app.queue.getByRole('button', {name:/^待核实时间/}).click();
    await expect(app.queue).toContainText('直播或首映的结束时间尚未可靠核实');
    await app.scan();
    expect((await app.call('activity.snapshot')).awaiting_timing).toBe(1);
  } finally {await app.close();}
});


test('matching translation checkpoint survives a 100-hour restart without repeating completed work', async ({page, request}) => {
  test.setTimeout(90000);
  const app = await harness(page, request, [{id:'checkpoint-video', ts:NOW - 71 * HOUR}]);
  const calls = async () => (await readFile(join(app.dir, 'model-calls'), 'utf8')).trim().split('\n');
  try {
    await app.write('classification-release');
    await app.write('captions-release');
    await app.write('model-ready');
    await app.write('first-model-response');
    await app.toggle(true);
    await expect.poll(async () => {try {return (await calls()).length;} catch {return 0;}}).toBe(2);
    const initial = (await calls())[0];
    await app.stop(true);
    await app.clock(NOW + 29 * HOUR);
    await app.write('model-release');
    await app.start();
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:20000}).toBe(1);
    expect((await calls()).filter(fingerprint => fingerprint === initial)).toHaveLength(1);
    const document = await app.call('documents.get', {video_id:'checkpoint-video'});
    expect(document.markdown).toContain('The complete source remains available.');
    const library = await app.call('library.inspect', {video_id:'checkpoint-video'});
    expect(library.source_trace.translation_available).toBe(true);
    await page.getByRole('button', {name:'阅读文档', exact:true}).click();
    await page.getByRole('combobox', {name:'按时间筛选'}).selectOption({label:'全部时间'});
    await expect(page.getByRole('button', {name:/checkpoint-video/})).toBeVisible();
  } finally {await app.close();}
});


for (const crossBeforeStart of [false, true]) {
  test(`retained captions first enter translation ${crossBeforeStart ? 'after' : 'inside'} the window`, async ({page, request}) => {
    test.setTimeout(60000);
    const app = await harness(page, request, [], false, true);
    try {
      await expect(app.queue).toContainText('尚未实际开工');
      await app.write('model-ready');
      await app.toggle(true);
      await expect.poll(() => app.exists('classification-retained-video')).toBe(true);
      if (crossBeforeStart) await app.clock(NOW + 2 * HOUR);
      await app.write('classification-release');
      if (crossBeforeStart) {
        await expect.poll(async () => (await app.call('activity.snapshot')).expired).toBe(1);
        expect(await app.exists('model-started')).toBe(false);
      } else {
        await expect.poll(() => app.exists('model-started')).toBe(true);
        expect((await app.call('activity.snapshot')).current[0].commenced_at).toBe(NOW);
        await app.stop(true);
        await app.clock(NOW + 29 * HOUR);
        await app.write('model-release');
        await app.start();
        await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:15000}).toBe(1);
      }
      expect(await app.exists('caption-retained-video')).toBe(false);
    } finally {await app.close();}
  });
}
