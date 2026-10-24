import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import type { Page, APIRequestContext } from '@playwright/test';

/** This file's scenarios share transport/process setup; assertions stay local. */
async function cutoverHarness(page: Page, request: APIRequestContext) {
  const root = await mkdtemp(join(tmpdir(), 'edison-cutover-'));
  let backend: ChildProcess | undefined;
  let url = '';
  await writeFile(join(root, 'clock'), String(Date.now() / 1000));
  async function start(bootstrap?: string) {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py', root], {
      cwd: resolve('../..'), stdio: ['ignore', 'pipe', 'pipe'],
      env: {...process.env, ...(bootstrap ? {EDISON_CUTOVER_STARTED_AT: bootstrap} : {}),
        PYTHONPATH: `${resolve('../..')}:${resolve('../../src')}`},
    });
    let logs = ''; backend.stderr!.on('data', data => logs += data);
    const port = await new Promise<number>((done, reject) => {
      let output = ''; const timer = setTimeout(() => reject(new Error(logs || 'backend timeout')), 20000);
      backend!.stdout!.on('data', data => {
        output += data; const line = output.split('\n').find(line => line.startsWith('{"port":'));
        if (line) { clearTimeout(timer); done(JSON.parse(line).port); }
      });
      backend!.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
    });
    url = `http://127.0.0.1:${port}`;
    await expect.poll(async () => { try { return (await request.get(url + '/v1/health')).status(); } catch { return 0; } }).toBe(200);
  }
  async function stop(signal: NodeJS.Signals = 'SIGTERM') {
    if (backend?.exitCode === null) {
      const ended = new Promise<void>(done => backend!.once('exit', () => done()));
      backend.kill(signal); await ended;
    }
  }
  async function open() {
    await page.route('**/v1/youtube/capability', async route => {
      const response = await route.fetch({url: url + '/v1/youtube/capability',
        headers: {...route.request().headers(), 'X-OpenWorker-Token': 'content-e2e'}}).catch(() => null);
      if (response) await route.fulfill({response}); else await route.abort('connectionrefused');
    });
    await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
    await page.goto('/'); await page.getByTestId('nav-youtube').click();
  }
  const raw = (capability: string, args = {}) => page.evaluate(async ({capability, args}) =>
    (await fetch('/v1/youtube/capability', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({capability, arguments: args})})).json(), {capability, args});
  const call = async (capability: string, args = {}) => {
    const value = await raw(capability, args); expect(value.ok, value.error).toBe(true); return value.result;
  };
  async function cleanup() {
    await rm(join(root, 'readonly-storage'), {force: true});
    await rm(join(root, 'hold-subscriptions'), {force: true});
    await page.unrouteAll({behavior: 'wait'}); await stop(); await rm(root, {recursive: true, force: true});
  }
  return {root, start, stop, open, raw, call, cleanup};
}

test('formal cutover validation keeps admission closed through proved connection checks and clean restart', async ({page, request}, testInfo) => {
  test.setTimeout(120000);
  const {root, start, stop, open, raw, call, cleanup} = await cutoverHarness(page, request);
  try {
    await writeFile(join(root,'subscription-videos.json'), JSON.stringify(['automatic01']));
    await start();
    await open();
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('cutover.begin');
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    expect((await raw('cutover.validation.begin', {expected_client_id:'fixture'})).ok).toBe(false);
    await call('cutover.backup');await call('cutover.migrate',{expected_client_id:'fixture'});
    await page.reload();await page.getByTestId('nav-youtube').click();
    await page.getByLabel('正式授权应用标识').fill('fixture', {timeout:3000});
    await page.getByRole('button',{name:'开始正式连接验证',exact:true}).click();
    await expect(page.getByRole('region',{name:'YouTube 切换暂停'})).toContainText('正式连接验证中');
    const started=await call('cutover.status');
    expect(started.state).toBe('validating');
    expect((await raw('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'blocked'})).kind).toBe('cutover_hold');
    expect((await raw('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'})).kind).toBe('cutover_hold');
    expect((await raw('cutover.complete',{official:true,content:true,restart:true})).ok).toBe(false);
    const validate=(capability:string,args={})=>call('cutover.validate',{capability,arguments:args});
    await validate('connection.check');
    await page.getByRole('button',{name:'打开正式连接验证',exact:true}).click();
    await page.getByRole('button',{name:'打开登录画面',exact:true}).click();
    await expect(page.getByAltText('服务器登录页面')).toBeVisible();
    await new Promise(done=>setTimeout(done,500));
    expect((await call('content_session.status')).browser_running).toBe(true);
    const cache=await page.evaluate(async()=>{
      const response=await fetch('/v1/youtube/capability',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capability:'cutover.validate',arguments:{capability:'content_session.screen'}})});
      return response.headers.get('Cache-Control');
    });
    expect(cache).toBe('no-store');

    await page.getByAltText('服务器登录页面').click();
    await page.getByRole('checkbox',{name:'我确认两条连接使用同一 Google 账号（用户确认，系统未自动核验）'}).check();
    await page.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'完成登录并检查字幕',exact:true}).click();
    await expect.poll(async()=>(await call('cutover.status')).validation.proofs.initial).toBeTruthy();
    await writeFile(join(root,'clock'),String(Number(await readFile(join(root,'clock'),'utf8'))+20));
    await validate('content_session.verify',{video_id:'abcdefghijk'});
    const proof=(await call('cutover.status')).validation.proofs;
    expect(proof.official).toBeTruthy();expect(proof.initial).toBeTruthy();expect(proof.closed).toBeTruthy();
    const evidence=await call('acceptance.export');
    expect(evidence.events.some((event:{kind:string;detail:{identity?:string;reason?:string}})=>event.kind==='intervention'&&event.detail.identity==='content'&&event.detail.reason==='first_connection')).toBe(true);
    await page.keyboard.press('Escape');
    await page.reload();await page.getByTestId('nav-youtube').click();
    await expect(page.getByRole('region',{name:'YouTube 切换暂停'})).toContainText('订阅刷新：已通过 · 同账号字幕：已通过 · 关闭浏览器后字幕：已通过');
    await page.screenshot({path:testInfo.outputPath('cutover-validation.png'),fullPage:true});
    await call('cutover.prepare_restart');
    await stop();await start();
    const restored=await call('cutover.status');
    expect(restored.state).toBe('validating');
    expect(restored.deadline_at).toBe(started.deadline_at);
    expect((await raw('cutover.complete')).ok).toBe(false);
    await validate('connection.check');
    await writeFile(join(root,'clock'),String(Number(await readFile(join(root,'clock'),'utf8'))+20));
    await validate('content_session.verify',{video_id:'abcdefghijk'});
    const captions=(await readFile(join(root,'caption-requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    expect(captions).toHaveLength(3);expect(new Set(captions.map(row=>row.pid)).size).toBe(3);
    await writeFile(join(root,'readonly-storage'),'');
    const failed=await raw('cutover.complete').catch(()=>({ok:false,kind:'http_failure'}));
    await rm(join(root,'readonly-storage'));
    expect(failed.kind).toBe('storage');
    expect((await call('cutover.status')).held).toBe(true);
    const complete=await call('cutover.complete');expect(complete.held).toBe(false);
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    await expect.poll(async()=>(await call('activity.snapshot')).runtime.worker_alive).toBe(true);
    expect((await call('acceptance.begin')).state).toBe('observing');


  } finally {await cleanup();}
});

test('maintenance bootstrap precedes workers and never renews its original deadline', async ({page, request}) => {
  test.setTimeout(120000);
  const {root, start, stop, open, raw, call, cleanup} = await cutoverHarness(page, request);
  let bootstrap: string | undefined;
  try {
    await writeFile(join(root,'model-unavailable'),'');
    await start(bootstrap);
    await open();
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('activity.resume');
    await call('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'before-maintenance'});
    await stop();
    const requests=await readFile(join(root,'google-requests.jsonl'),'utf8');
    const started=Number(await readFile(join(root,'clock'),'utf8'))-100;
    bootstrap=String(started);
    await start(bootstrap);
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    const held=await call('cutover.status');
    expect(held.held).toBe(true);expect(held.deadline_at).toBe(started+1800);
    expect((await call('activity.snapshot')).drain_paused).toBe(false);
    expect((await call('activity.snapshot')).runtime.worker_alive).toBe(false);
    expect((await call('activity.snapshot')).queued).toBe(1);
    expect(await readFile(join(root,'google-requests.jsonl'),'utf8')).toBe(requests);
    await writeFile(join(root,'clock'),String(started+1801));
    expect((await call('cutover.status')).state).toBe('aborted');
    expect((await raw('cutover.validation.begin',{expected_client_id:'fixture'})).ok).toBe(false);
    await stop();bootstrap=String(started+1801);await start(bootstrap);
    const retried=await call('cutover.begin');
    expect(retried.state).toBe('aborted');expect(retried.deadline_at).toBe(started+1800);
    expect((await call('activity.snapshot')).queued).toBe(1);
    expect(await readFile(join(root,'google-requests.jsonl'),'utf8')).toBe(requests);
  } finally {await cleanup();}
});

test('validation withdrawal and deadline reject stale proofs and unplanned restart', async ({page, request}) => {
  test.setTimeout(120000);
  const {root, start, stop, open, raw, call, cleanup} = await cutoverHarness(page, request);
  try {
    await writeFile(join(root,'subscription-videos.json'),JSON.stringify(['automatic01']));
    await start();
    await open();
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('cutover.begin');
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    await call('cutover.backup');await call('cutover.migrate',{expected_client_id:'fixture'});
    await call('cutover.validation.begin',{expected_client_id:'fixture'});
    await writeFile(join(root,'oauth-invalid-grant'),'');
    const rejected=await raw('cutover.validate',{capability:'connection.check'});
    expect(rejected.ok).toBe(false);
    expect((await call('connection.status')).subscription_capability.state).toBe('interrupted');
    await rm(join(root,'oauth-invalid-grant'));
    await call('cutover.validate',{capability:'connection.check'});
    const original=await call('cutover.status');
    await writeFile(join(root,'hold-subscriptions'),'');
    const checking=raw('cutover.validate',{capability:'connection.check'});
    await expect.poll(async()=>{try{await readFile(join(root,'subscriptions-held'));return true;}catch{return false;}}).toBe(true);
    const started=Date.now();
    await call('connection.disconnect');
    expect(Date.now()-started).toBeLessThan(2000);
    await rm(join(root,'hold-subscriptions'));
    expect((await checking).ok).toBe(false);
    const withdrawn=await call('cutover.status');
    expect(withdrawn.state).toBe('aborted');expect(withdrawn.validation.proofs).toEqual({});
    expect((await raw('cutover.complete',{official:true,restart:true})).ok).toBe(false);
    await stop();await start();
    expect((await call('cutover.status')).deadline_at).toBe(original.deadline_at);
    expect((await call('connection.status')).authorized).toBe(false);
  } finally {await cleanup();}
});

test('validation deadline closes its browser and never releases acquisition', async ({page, request}) => {
  test.setTimeout(120000);
  const {root, start, open, raw, call, cleanup} = await cutoverHarness(page, request);
  try {
    await writeFile(join(root,'subscription-videos.json'),JSON.stringify(['automatic01']));
    await start();
    await open();
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('cutover.begin');
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    await call('cutover.backup');await call('cutover.migrate',{expected_client_id:'fixture'});
    await call('cutover.validation.begin',{expected_client_id:'fixture'});
    const original=await call('cutover.status');
    await call('cutover.validate',{capability:'content_session.open'});
    await writeFile(join(root,'clock'),String(original.deadline_at+1));
    await expect.poll(async()=>(await call('cutover.status')).state).toBe('aborted');
    await expect.poll(async()=>(await call('content_session.status')).browser_running).toBe(false);
    expect((await call('cutover.status')).deadline_at).toBe(original.deadline_at);
    expect((await raw('cutover.prepare_restart')).ok).toBe(false);
    expect((await raw('cutover.complete',{official:true,restart:true})).ok).toBe(false);
    expect((await raw('cutover.validate',{capability:'connection.check'})).kind).toBe('cutover_hold');
    expect((await raw('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'still-blocked'})).kind).toBe('cutover_hold');
  } finally {await cleanup();}
});

test('unplanned validation restart aborts and retains the original deadline', async ({page, request}) => {
  test.setTimeout(120000);
  const {root, start, stop, open, raw, call, cleanup} = await cutoverHarness(page, request);
  try {
    await writeFile(join(root,'subscription-videos.json'),JSON.stringify(['automatic01']));
    await start();
    await open();
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('cutover.begin');
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    await call('cutover.backup');await call('cutover.migrate',{expected_client_id:'fixture'});
    await call('cutover.validation.begin',{expected_client_id:'fixture'});
    const original=await call('cutover.status');
    await call('cutover.validate',{capability:'content_session.open'});
    await stop('SIGKILL');await start();
    expect((await call('cutover.status')).state).toBe('aborted');
    expect((await call('cutover.status')).deadline_at).toBe(original.deadline_at);
    expect((await raw('cutover.prepare_restart')).ok).toBe(false);
    expect((await raw('cutover.complete',{official:true,restart:true})).ok).toBe(false);
    expect((await raw('cutover.validate',{capability:'connection.check'})).kind).toBe('cutover_hold');
    expect((await raw('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'still-blocked'})).kind).toBe('cutover_hold');
  } finally {await cleanup();}
});
