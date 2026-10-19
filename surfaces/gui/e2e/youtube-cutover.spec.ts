import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('cutover hold is independent of Auto Update and persists as read-only after restart', async ({page, request}, testInfo) => {
  test.setTimeout(60000);
  const root = await mkdtemp(join(tmpdir(), 'edison-cutover-'));
  let backend: ChildProcess | undefined;
  let url = '';
  await writeFile(join(root,'clock'),String(Date.now()/1000));
  async function start() {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py',root], {
      cwd:resolve('../..'),env:{...process.env,PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`},stdio:['ignore','pipe','pipe'],
    });
    let logs=''; backend.stderr!.on('data',data=>logs+=data);
    const port=await new Promise<number>((done,reject)=>{
      let output='';const timer=setTimeout(()=>reject(new Error(logs||'backend timeout')),20000);
      backend!.stdout!.on('data',data=>{output+=data;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
      backend!.once('exit',()=>{clearTimeout(timer);reject(new Error(logs));});
    });
    url=`http://127.0.0.1:${port}`;
    await expect.poll(async()=>{try{return(await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  }
  async function stop(){if(backend?.exitCode===null){const ended=new Promise<void>(done=>backend!.once('exit',()=>done()));backend.kill('SIGTERM');await ended;}}
  const raw = (capability:string,args={})=>page.evaluate(async({capability,args})=>(await fetch('/v1/youtube/capability',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capability,arguments:args})})).json(),{capability,args});
  const call = async(capability:string,args={})=>{const value=await raw(capability,args);expect(value.ok,value.error).toBe(true);return value.result;};
  try {
    await start();
    await page.route('**/v1/youtube/capability',async route=>{
      const response=await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
    await page.goto('/');await page.getByTestId('nav-youtube').click();
    await call('activity.resume');
    await page.getByText('维护工具',{exact:true}).click();
    await page.getByRole('button',{name:'开始切换暂停',exact:true}).click();
    await expect(page.getByRole('region',{name:'YouTube 切换暂停'})).toBeVisible();
    const held=await call('cutover.status');
    expect(held.held).toBe(true);
    expect(held.deadline_at-held.started_at).toBe(1800);
    expect((await call('activity.snapshot')).drain_paused).toBe(false);
    expect((await raw('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'hold-blocked'})).kind).toBe('cutover_hold');
    expect((await raw('connection.import_authorization',{client_id:'fixture',client_secret:'fixture',refresh_token:'fixture'})).kind).toBe('cutover_hold');
    await expect.poll(async()=>(await call('cutover.status')).state).toBe('ready');
    await stop();await start();
    const restored=await call('cutover.status');
    expect(restored.state).toBe('aborted');
    await page.reload();await page.getByTestId('nav-youtube').click();
    await expect(page.getByRole('region',{name:'YouTube 切换暂停'})).toContainText('服务重启已中止切换');
    await page.screenshot({path:testInfo.outputPath('cutover-paused.png'),fullPage:true});
    expect(restored.reason).toContain('重启');
    expect((await call('activity.snapshot')).drain_paused).toBe(false);
    expect((await raw('manual.submit',{url:'https://youtu.be/abcdefghijk',request_id:'hold-blocked'})).kind).toBe('cutover_hold');
  } finally {await page.unrouteAll({behavior:'wait'});await stop();await rm(root,{recursive:true,force:true});}
});

test('cutover deadline retains long task results and latest withdrawals while all writers drain', async ({page, request}) => {
  test.setTimeout(120000);
  const root = await mkdtemp(join(tmpdir(), 'edison-cutover-'));
  let backend: ChildProcess | undefined;
  let url = '';
  await writeFile(join(root,'clock'),String(Date.now()/1000));
  async function start() {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py',root], {
      cwd:resolve('../..'),env:{...process.env,PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`},stdio:['ignore','pipe','pipe'],
    });
    let logs=''; backend.stderr!.on('data',data=>logs+=data);
    const port=await new Promise<number>((done,reject)=>{
      let output='';const timer=setTimeout(()=>reject(new Error(logs||'backend timeout')),20000);
      backend!.stdout!.on('data',data=>{output+=data;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
      backend!.once('exit',()=>{clearTimeout(timer);reject(new Error(logs));});
    });
    url=`http://127.0.0.1:${port}`;
    await expect.poll(async()=>{try{return(await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  }
  async function stop(){if(backend?.exitCode===null){const ended=new Promise<void>(done=>backend!.once('exit',()=>done()));backend.kill('SIGTERM');await ended;}}
  const raw = (capability:string,args={})=>page.evaluate(async({capability,args})=>(await fetch('/v1/youtube/capability',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capability,arguments:args})})).json(),{capability,args});
  const call = async(capability:string,args={})=>{const value=await raw(capability,args);expect(value.ok,value.error).toBe(true);return value.result;};
  try {
    await start();
    await page.route('**/v1/youtube/capability',async route=>{
      const response=await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
    await page.goto('/');await page.getByTestId('nav-youtube').click();
    let clock=Date.now()/1000;
    const advance=async(seconds=20)=>{clock+=seconds;await writeFile(join(root,'clock'),String(clock));};
    const exists=async(name:string)=>{try{await readFile(join(root,name));return true;}catch{return false;}};
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('content_session.open');await call('content_session.click',{x:1,y:1});
    await call('content_session.confirm',{video_id:'abcdefghijk',same_account:true});
    await call('manual.submit',{url:'https://youtu.be/keptvideo01',request_id:'kept-document'});
    await expect.poll(async()=>{await advance();return(await call('library.inspect',{video_id:'keptvideo01'})).manuscript_version;},{timeout:20000}).toBe(1);
    await call('library.set_reading_state',{video_id:'keptvideo01',reading_state:'read'});
    const kept=await call('library.inspect',{video_id:'keptvideo01'});
    await writeFile(join(root,'model-unavailable'),'');
    await writeFile(join(root,'subscription-videos.json'),JSON.stringify(['automatic01','automatic02']));
    await call('connection.refresh_subscriptions');await call('activity.resume');
    await expect.poll(async()=>{await advance();return(await call('activity.snapshot')).queued;},{timeout:15000}).toBe(2);
    await call('manual.submit',{url:'https://youtu.be/manualvideo',request_id:'manual-before-hold'});
    await writeFile(join(root,'model-hold'),'');await rm(join(root,'model-unavailable'));
    await expect.poll(async()=>{await advance();return exists('model-active');},{timeout:20000}).toBe(true);
    const current=(await call('activity.snapshot')).current[0].video_id;
    const cancelled=['automatic01','automatic02','manualvideo'].find(id=>id!==current)!;
    await writeFile(join(root,'hold-subscriptions'),'');
    const official=raw('connection.refresh_subscriptions');
    await expect.poll(()=>exists('subscriptions-held')).toBe(true);
    const held=await call('cutover.begin');expect(held.state).toBe('draining');
    for(const capability of ['manual.submit','manual.restore','manual.retry','activity.restore','library.regenerate','summary.ensure','session_cookies.replace','session_cookies.test','connection.configure','connection.authorize','connection.migrate_credentials','connection.import_authorization','collection.refresh_updates','content_session.open','content_session.verify']) {
      expect((await raw(capability,{url:'https://youtu.be/newvideo001',request_id:'blocked-new-intent',video_id:current})).kind,capability).toBe('cutover_hold');
    }
    await call('activity.cancel',{video_ids:[cancelled]});
    await call('connection.disconnect');
    await call('activity.drain_pause');await call('activity.resume');
    await advance(1801);
    await expect.poll(async()=>(await call('cutover.status')).state).toBe('aborted');
    expect((await call('cutover.status')).drained).toBe(false);
    expect((await call('activity.snapshot')).generating).toBe(1);
    expect((await call('library.inspect',{video_id:cancelled})).preparation_state).toBe('cancelled');
    await rm(join(root,'hold-subscriptions'));
    expect((await official).ok).toBe(false);
    await rm(join(root,'model-hold'));
    await expect.poll(async()=>(await call('library.inspect',{video_id:current})).manuscript_version,{timeout:20000}).toBe(1);
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    const drained=await call('cutover.status');expect(drained.state).toBe('aborted');
    const activity=await call('activity.snapshot');expect(activity.drain_paused).toBe(false);expect(activity.runtime.worker_alive).toBe(false);expect(activity.runtime.discovery_worker_alive).toBe(false);
    expect((await call('connection.status')).authorized).toBe(false);
    expect(await call('library.inspect',{video_id:'keptvideo01'})).toEqual(kept);
    const requestsBefore=await readFile(join(root,'google-requests.jsonl'),'utf8');
    await advance(86400);await stop();await start();
    await expect.poll(async()=>(await call('cutover.status')).drained).toBe(true);
    expect((await call('cutover.status')).state).toBe('aborted');
    expect((await call('activity.snapshot')).queued).toBe(1);
    expect((await call('activity.snapshot')).drain_paused).toBe(false);
    expect((await call('library.inspect',{video_id:cancelled})).preparation_state).toBe('cancelled');
    expect(await call('library.inspect',{video_id:'keptvideo01'})).toEqual(kept);
    expect(await readFile(join(root,'google-requests.jsonl'),'utf8')).toBe(requestsBefore);
    expect((await call('documents.get',{video_id:current})).markdown).toBeTruthy();
  } finally {
    await rm(join(root,'model-hold'),{force:true});await rm(join(root,'hold-subscriptions'),{force:true});
    try {await call('cutover.begin');await expect.poll(async()=>{const value=Number(await readFile(join(root,'clock'),'utf8'));await writeFile(join(root,'clock'),String(value+30));return(await call('cutover.status')).drained;},{timeout:10000}).toBe(true);} catch {}
    await page.unrouteAll({behavior:'wait'});await stop();await rm(root,{recursive:true,force:true});
  }
});

test('cutover waits for actual caption child exit and saved manuscript', async ({page, request}) => {
  test.setTimeout(60000);
  const root = await mkdtemp(join(tmpdir(), 'edison-cutover-'));
  let backend: ChildProcess | undefined;
  let url = '';
  await writeFile(join(root,'clock'),String(Date.now()/1000));
  async function start() {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py',root], {
      cwd:resolve('../..'),env:{...process.env,PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`},stdio:['ignore','pipe','pipe'],
    });
    let logs=''; backend.stderr!.on('data',data=>logs+=data);
    const port=await new Promise<number>((done,reject)=>{
      let output='';const timer=setTimeout(()=>reject(new Error(logs||'backend timeout')),20000);
      backend!.stdout!.on('data',data=>{output+=data;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
      backend!.once('exit',()=>{clearTimeout(timer);reject(new Error(logs));});
    });
    url=`http://127.0.0.1:${port}`;
    await expect.poll(async()=>{try{return(await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  }
  async function stop(){if(backend?.exitCode===null){const ended=new Promise<void>(done=>backend!.once('exit',()=>done()));backend.kill('SIGTERM');await ended;}}
  const raw = (capability:string,args={})=>page.evaluate(async({capability,args})=>(await fetch('/v1/youtube/capability',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capability,arguments:args})})).json(),{capability,args});
  const call = async(capability:string,args={})=>{const value=await raw(capability,args);expect(value.ok,value.error).toBe(true);return value.result;};
  try {
    await start();
    await page.route('**/v1/youtube/capability',async route=>{
      const response=await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
    await page.goto('/');await page.getByTestId('nav-youtube').click();
    let clock=Date.now()/1000;
    const advance=async()=>{clock+=20;await writeFile(join(root,'clock'),String(clock));};
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'});
    await call('content_session.open');await call('content_session.click',{x:1,y:1});
    await call('content_session.confirm',{video_id:'abcdefghijk',same_account:true});
    await advance();await writeFile(join(root,'caption-mode'),'hold');
    await call('manual.submit',{url:'https://youtu.be/captionwork',request_id:'caption-drain-test'});
    let pid=0;
    await expect.poll(async()=>{await advance();try{pid=Number(await readFile(join(root,'caption-held'),'utf8'));return pid;}catch{return 0;}},{timeout:15000}).toBeGreaterThan(0);
    await call('cutover.begin');
    expect((await call('cutover.status')).drained).toBe(false);
    expect((await call('activity.snapshot')).acquiring).toBe(1);
    expect(()=>process.kill(pid,0)).not.toThrow();
    await writeFile(join(root,'caption-release'),'');
    await expect.poll(async()=>{await advance();return(await call('cutover.status')).drained;},{timeout:15000}).toBe(true);
    expect(()=>process.kill(pid,0)).toThrow();
    expect((await call('library.inspect',{video_id:'captionwork'})).manuscript_version).toBe(1);
    expect((await call('library.inspect',{video_id:'captionwork'})).source_trace.transcript_available).toBe(true);
    expect((await call('documents.get',{video_id:'captionwork'})).markdown).toContain('full original source');
  } finally {
    await writeFile(join(root,'caption-release'),'');
    try {await call('cutover.begin');await expect.poll(async()=>{const value=Number(await readFile(join(root,'clock'),'utf8'));await writeFile(join(root,'clock'),String(value+30));return(await call('cutover.status')).drained;},{timeout:10000}).toBe(true);} catch {}
    await page.unrouteAll({behavior:'wait'});await stop();await rm(root,{recursive:true,force:true});
  }
});
