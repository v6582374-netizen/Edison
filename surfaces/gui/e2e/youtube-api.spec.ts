import {test, expect} from './fixtures';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile, readFile, rm, rename, access} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import type {Page, APIRequestContext} from '@playwright/test';

type Scenario = {model_failure?: boolean; retry_after?: string; caption_failure?: string; hold_classification?: boolean; platform_pages?: boolean; channels?: string[]; videos?: {id: string; published: number; added?: number; channel?: string; kind?: string; claimed_publication?: number; live?: Record<string, string>; caption_status?: string; shorts?: boolean | null; listed?: boolean}[];
  global_failure?: string; failure_endpoint?: string; auth_failure?: boolean; channel_failure?: string; caption_cooldown?: boolean; more_pages?: boolean};
async function harness(page: Page, request: APIRequestContext, scenario: Scenario, releaseModel = true) {
  const root = await mkdtemp(join(tmpdir(), 'edison-api-'));
  const now = Math.floor(Date.now()/1000);
  const write = async (name: string, value='go') => {await writeFile(join(root,name+'.tmp'),value);await rename(join(root,name+'.tmp'),join(root,name));};
  const exists = async (name: string) => {try{await access(join(root,name));return true;}catch{return false;}};
  await write('clock',String(now));
  await write('scenario.json',JSON.stringify(scenario));
  if(releaseModel) await write('model-release');
  let backend: ReturnType<typeof spawn>;
  let url = '';
  const start = async () => {
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-api-server.py',root],{cwd:resolve('../..'),stdio:['ignore','pipe','pipe']});
  let logs=''; backend.stderr!.on('data',chunk=>logs+=chunk);
  const port=await new Promise<number>((done,reject)=>{
    let output='';const timer=setTimeout(()=>reject(new Error(logs)),30000);
    backend.stdout!.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
    backend.once('exit',code=>{clearTimeout(timer);reject(new Error(`${code}: ${logs}`));});
  });
  url=`http://127.0.0.1:${port}`;
  await expect.poll(async()=>{try{return (await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  };
  const stop = async (signal: NodeJS.Signals = 'SIGKILL') => {
    if (backend.exitCode === null) {
      const exited = new Promise<void>(done => backend.once('exit', () => done()));
      backend.kill(signal);
      await exited;
    }
  };
  await start();
  const call=async(capability: string,args={})=>{
    const response=await request.post(url+'/v1/youtube/capability',{headers:{'X-OpenWorker-Token':'youtube-api-e2e'},data:{capability,arguments:args}});
    const payload=await response.json();expect(payload.ok,payload.error).toBe(true);return payload.result;
  };
  await page.route('**/v1/youtube/capability',async route=>{
    try {const response=await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'youtube-api-e2e'}});await route.fulfill({response});}
    catch {await route.abort();}
  });
  await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
  await page.goto('/');await page.getByTestId('nav-youtube').click();
  const authorize=async()=>{
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog',{name:'订阅频道',exact:true}).getByRole('button',{name:'连接 YouTube',exact:true}).click();
    await page.getByRole('button',{name:'在浏览器中授权并导入订阅',exact:true}).click();
    await expect(page.getByText(/YouTube 已连接，已导入 \d+ 个订阅。/)).toBeVisible();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
  };
  await authorize();
  await page.getByRole('button',{name:'处理进度',exact:true}).click();
  const toggle=async(on: boolean)=>{
    await page.getByRole('button',{name:on?'恢复自动更新':'完成当前文档后暂停',exact:true}).click();
    await expect(page.getByRole('button',{name:on?'完成当前文档后暂停':'恢复自动更新',exact:true})).toBeEnabled();
  };
  return {root,now,write,exists,call,toggle,authorize,start,stop,
    scenario:(value: Scenario)=>write('scenario.json',JSON.stringify(value)),
    clock:(value: number)=>write('clock',String(value)),
    requests:async()=>{try{return (await readFile(join(root,'api-requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));}catch{return []; }},
    close:async()=>{await stop();await rm(root,{recursive:true,force:true});},
  };
}

test('official uploads discovery prepares a new video using its public time',async({page,request})=>{
  test.setTimeout(60000);
  const now=Math.floor(Date.now()/1000);
  const app=await harness(page,request,{videos:[{id:'fresh-video',published:now-60,added:now-100*3600}]});
  try{
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:15000}).toBe(1);
    expect((await app.requests()).map(r=>r.endpoint)).toEqual(expect.arrayContaining(['subscriptions','channels','playlistItems','videos']));
    await page.getByRole('button',{name:'阅读文档',exact:true}).click();
    await expect(page.getByRole('button',{name:/fresh-video/})).toBeVisible();
  }finally{await app.close();}
});

test('five-minute discovery continues during a long model request',async({page,request})=>{
  test.setTimeout(60000);
  const now=Math.floor(Date.now()/1000);
  const app=await harness(page,request,{videos:[{id:'slow-model',published:now-60}]},false);
  try{
    await app.toggle(true);
    await expect.poll(()=>app.exists('model-started'),{timeout:15000}).toBe(true);
    await app.scenario({videos:[{id:'slow-model',published:now-60},{id:'discovered-during-model',published:now+200}]});
    await app.clock(app.now+300);
    const queue=page.getByRole('region',{name:'处理队列',exact:true});
    await expect(queue).toContainText('discovered-during-model',{timeout:10000});
    expect((await app.call('activity.snapshot')).generating).toBe(1);
    expect((await app.requests()).filter(r=>r.endpoint==='subscriptions')).toHaveLength(1);
  }finally{await app.close();}
});

test('caption cooldown and one unavailable channel do not block discovery; pause blocks the next scan',async({page,request})=>{
  const now=Math.floor(Date.now()/1000);
  const initial: Scenario={channels:['alpha','beta'],channel_failure:'alpha',caption_cooldown:true,videos:[{id:'caption-wait',channel:'beta',published:now-30}]};
  const app=await harness(page,request,initial);
  try {
    await app.toggle(true);
    await expect.poll(()=>app.exists('caption-caption-wait')).toBe(true);
    await app.scenario({...initial,videos:[...initial.videos!,{id:'during-cooldown',channel:'beta',published:now+200}]});
    await app.clock(app.now+300);
    await expect(page.getByRole('region',{name:'处理队列',exact:true})).toContainText('during-cooldown');
    await expect(page.getByRole('region',{name:'订阅发现',exact:true}).getByText('部分频道不可用，其他频道继续检查。')).toBeVisible();
    await app.toggle(false);
    const count=(await app.requests()).length;
    await app.clock(app.now+600);
    await page.waitForTimeout(1600);
    expect((await app.requests()).length).toBe(count);
  } finally {await app.close();}
});

for (const mode of ['quota','service','authorization']) {
  test(`global ${mode} gate stops channels and recovers only with Auto Update enabled`,async({page,request})=>{
    test.setTimeout(60000);
    const app=await harness(page,request,{channels:['alpha','beta']});
    try {
      await app.scenario({channels:['alpha','beta'],global_failure:mode});
      await app.toggle(true);
      await expect.poll(async()=>(await app.call('activity.snapshot')).discovery.status).toBe(mode);
      const failed=await app.requests();
      expect(failed.filter(r=>r.endpoint!=='subscriptions')).toHaveLength(mode==='authorization'?2:1);
      await app.toggle(false);
      await app.scenario({channels:['alpha','beta'],videos:[{id:'after-recovery',published:app.now+86400}]});
      await app.clock(app.now+86400);
      await page.waitForTimeout(1600);
      expect((await app.requests()).length).toBe(failed.length);
      if(mode==='authorization') {
        await app.authorize();
        await page.getByRole('button',{name:'处理进度',exact:true}).click();
      }
      await app.toggle(true);
      await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:15000}).toBe(1);
    } finally {await app.close();}
  });
}

test('bounded scans disclose incomplete coverage and keep uncertain publication pending',async({page,request})=>{
  const now=Math.floor(Date.now()/1000);
  const app=await harness(page,request,{more_pages:true,videos:[
    {id:'old-first',published:now-100*3600}, {id:'ordinary',published:now-60},
    {id:'premiere',published:now-60,kind:'upcoming'},
    {id:'conflicting-time',published:now-60,claimed_publication:now-100*3600},
  ]});
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
    const snapshot=await app.call('activity.snapshot');
    expect(snapshot.discovery.status).toBe('partial');
    expect(snapshot.discovery.budget.project_remaining_units).toBeNull();
    await page.getByText('来源状态与预算', {exact:true}).click();
    await expect(page.getByText(/本轮仅检查一页上传列表/)).toBeVisible();
    expect(await app.exists('caption-premiere')).toBe(false);
    expect(await app.exists('caption-conflicting-time')).toBe(false);
    await app.clock(app.now+300);
    await expect.poll(async()=>(await app.requests()).filter(r=>r.endpoint==='playlistItems').length).toBe(2);
    expect((await app.call('activity.snapshot')).ready).toBe(1);
  } finally {await app.close();}
});


test('first list observation survives a failed detail request',async({page,request})=>{
  const app=await harness(page,request,{});
  try {
    const videos=[{id:'observed-before-failure',published:app.now-60}];
    await app.scenario({videos,global_failure:'service',failure_endpoint:'videos'});
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).discovery.status).toBe('service');
    await app.scenario({videos});
    await app.clock(app.now+300);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
    const asset=await app.call('library.inspect',{video_id:'observed-before-failure'});
    expect(asset.source_observed_at).toBe(app.now);
    expect(asset.source_observed_from).toBe(app.now);
    expect(asset.discovered_at).toBe(app.now+300);
  } finally {await app.close();}
});

test('budget scale is visible and membership refresh remains separate',async({page,request})=>{
  const channels=Array.from({length:35},(_,i)=>`channel-${i}`);
  const app=await harness(page,request,{channels});
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).discovery.scanned_sources).toBe(35);
    await page.getByText('来源状态与预算',{exact:true}).click();
    await expect(page.getByText('订阅规模超过当前扫描或预算容量，无法覆盖所有频道的五分钟检查。')).toBeVisible();
    await app.clock(app.now+300);
    await expect.poll(async()=>(await app.requests()).filter(r=>r.endpoint==='playlistItems').length).toBe(70);
    expect((await app.requests()).filter(r=>r.endpoint==='subscriptions')).toHaveLength(1);
    await app.clock(app.now+21610);
    await expect.poll(async()=>(await app.requests()).filter(r=>r.endpoint==='subscriptions').length).toBe(2);
    await app.toggle(false);
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog',{name:'订阅频道',exact:true}).getByRole('button',{name:'同步订阅',exact:true}).click();
    await expect.poll(async()=>(await app.requests()).filter(r=>r.endpoint==='subscriptions').length).toBeGreaterThanOrEqual(3);
  } finally {await app.close();}
});


test('finished live streams and premieres use actual end time through the real workbench', async ({page, request}) => {
  test.setTimeout(60000);
  const now = Math.floor(Date.now()/1000);
  const old = now - 4*86400;
  const app = await harness(page, request, {platform_pages:true, videos: [
    {id:'liveDone001', published:old, live:{actualStartTime:new Date(old*1000).toISOString(), actualEndTime:new Date((now-60)*1000).toISOString()}, caption_status:'was_live'},
    {id:'premDone001', published:old-86400, live:{actualStartTime:new Date((now-3600)*1000).toISOString(), actualEndTime:new Date((now-60)*1000).toISOString()}, caption_status:'not_live'},
  ]});
  try {
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:15000}).toBe(2);
    const asset = await app.call('library.inspect', {video_id:'liveDone001'});
    expect(Date.parse(asset.published_at)/1000).toBe(old);
    expect(Date.parse(asset.actual_end_at)/1000).toBe(now-60);
    expect(asset.source_observed_at).toBe(app.now);
    expect(asset.timing_checked_at).toBe(app.now);
    expect((await app.call('activity.snapshot')).discovery.status).toBe('healthy');
    await page.getByRole('button', {name:'阅读文档',exact:true}).click();
    await expect(page.getByRole('button', {name:/liveDone001/})).toBeVisible();
    await expect(page.getByRole('button', {name:/premDone001/})).toBeVisible();
  } finally {await app.close();}
});

test('future, active and uncertain events wait without subtitle work and recover off the uploads page', async ({page, request}) => {
  test.setTimeout(60000);
  const app = await harness(page, request, {});
  const stamp = (value: number) => new Date(value*1000).toISOString();
  const videos: NonNullable<Scenario['videos']> = [
    {id:'futureEv001', published:app.now+86400, kind:'upcoming', live:{scheduledStartTime:stamp(app.now+86400)}},
    {id:'activeEv001', published:app.now-4*86400, kind:'live', live:{actualStartTime:stamp(app.now-4*86400)}},
    {id:'unknownE001', published:app.now-4*86400, live:{actualStartTime:stamp(app.now-3600)}},
    {id:'conflict001', published:app.now-4*86400, kind:'live', live:{actualStartTime:stamp(app.now-3600),actualEndTime:stamp(app.now-60)}},
    {id:'endFuture01', published:app.now-4*86400, live:{actualStartTime:stamp(app.now-3600),actualEndTime:stamp(app.now+3600)}},
    {id:'shortOrd001', published:app.now-60},
    {id:'platformS01', published:app.now-60, shorts:true},
    {id:'unknownS001', published:app.now-60, shorts:null},
  ];
  try {
    await app.scenario({platform_pages:true, videos});
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready).toBe(1);
    await expect.poll(async () => (await app.call('activity.snapshot')).filtered).toBe(1);
    const queue = page.getByRole('region', {name:'处理队列',exact:true});
    await queue.getByRole('button', {name:/^等待视频结束/}).click();
    await expect(queue.locator('li')).toHaveCount(2);
    await expect(queue).toContainText('futureEv001');
    await expect(queue).toContainText('activeEv001');
    const waiting = await app.call('activity.list', {state:'awaiting_completion'});
    expect(waiting.items.every((item: {commenced_at: number | null}) => item.commenced_at === null)).toBe(true);
    await queue.locator('li').filter({hasText:'futureEv001'}).getByRole('button', {name:'取消处理',exact:true}).click();
    await expect.poll(async () => (await app.call('activity.snapshot')).cancelled).toBe(1);
    await queue.getByRole('button', {name:/^待核实时间/}).click();
    await expect(queue.locator('li')).toHaveCount(3);
    expect((await app.call('activity.snapshot')).discovery.status).toBe('healthy');
    for (const v of videos.filter(v => v.id !== 'shortOrd001')) expect(await app.exists('caption-'+v.id)).toBe(false);
    const requests = await app.requests();
    await page.waitForTimeout(1200);
    expect((await app.requests()).length).toBe(requests.length);
    // Completion and first list observation are different events, even when the
    // video no longer appears on the first uploads page.
    await app.scenario({platform_pages:true, videos:videos.map(v => v.id === 'activeEv001'
      ? {...v, listed:false, kind:'none', caption_status:'was_live', live:{...v.live,actualEndTime:stamp(app.now+200)}} : v)});
    await app.clock(app.now+300);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready, {timeout:15000}).toBe(2);
    const asset = await app.call('library.inspect', {video_id:'activeEv001'});
    expect(asset.source_observed_at).toBe(app.now);
    expect(asset.timing_checked_at).toBe(app.now+300);
    expect((await app.call('library.inspect', {video_id:'futureEv001'})).preparation_state).toBe('cancelled');
    expect(Date.parse(asset.actual_end_at)/1000).toBe(app.now+200);
  } finally {await app.close();}
});

test('ended events include exactly 72 hours and exclude the next second without deleting old manuscripts', async ({page, request}) => {
  test.setTimeout(60000);
  const app = await harness(page, request, {});
  const live = (end: number) => ({actualStartTime:new Date((app.now-5*86400)*1000).toISOString(),actualEndTime:new Date(end*1000).toISOString()});
  const videos = [
    {id:'boundaryE01',published:app.now-6*86400,live:live(app.now-72*3600),caption_status:'was_live'},
    {id:'expiredEv01',published:app.now-6*86400,live:live(app.now-72*3600-1),caption_status:'was_live'},
  ];
  try {
    await app.scenario({platform_pages:true,videos});
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready).toBe(1);
    expect((await app.call('activity.snapshot')).expired).toBe(1);
    expect(await app.exists('caption-expiredEv01')).toBe(false);
    await page.getByRole('region', {name:'处理队列',exact:true}).getByRole('button', {name:/^已过期/}).click();
    await expect(page.getByText('expiredEv01', {exact:true})).toBeVisible();
    await app.clock(app.now+30*86400);
    await expect.poll(async () => (await app.requests()).filter(r => r.endpoint==='playlistItems').length).toBe(2);
    const document = await app.call('documents.get', {video_id:'boundaryE01'});
    expect(document.markdown).toContain('The full original source is retained.');
    await page.getByRole('button', {name:'阅读文档',exact:true}).click();
    await expect(page.getByRole('button', {name:/boundaryE01/})).toBeVisible();
  } finally {await app.close();}
});


test('commenced event work survives a crash beyond the window and waits for Auto Update', async ({page, request}) => {
  test.setTimeout(60000);
  const app = await harness(page, request, {}, false);
  try {
    await app.scenario({platform_pages:true,videos:[{id:'resumeEv001',published:app.now-6*86400,
      live:{actualStartTime:new Date((app.now-5*86400)*1000).toISOString(),actualEndTime:new Date((app.now-71*3600)*1000).toISOString()},caption_status:'was_live'}]});
    await app.toggle(true);
    await expect.poll(() => app.exists('model-started')).toBe(true);
    await app.toggle(false);
    await app.stop();
    await app.clock(app.now+29*3600);
    await app.write('model-release');
    await app.start();
    const queue = page.getByRole('region', {name:'处理队列',exact:true});
    await expect(queue).toContainText('已实际开工，可跨窗口续办');
    expect((await app.call('activity.snapshot')).ready).toBe(0);
    await app.toggle(true);
    await expect.poll(async () => (await app.call('activity.snapshot')).ready,{timeout:15000}).toBe(1);
    const asset = await app.call('library.inspect', {video_id:'resumeEv001'});
    expect(asset.source_trace.transcript_available).toBe(true);
    expect(asset.source_trace.translation_available).toBe(true);
    await page.getByRole('button', {name:'阅读文档',exact:true}).click();
    await expect(page.getByRole('button', {name:/resumeEv001/})).toBeVisible();
  } finally {await app.close();}
});


test('event classification does not grant commencement before the 72-hour window closes', async ({page, request}) => {
  const app = await harness(page, request, {});
  try {
    await app.scenario({platform_pages:true,hold_classification:true,videos:[{id:'crossingE01',published:app.now-6*86400,
      live:{actualStartTime:new Date((app.now-5*86400)*1000).toISOString(),actualEndTime:new Date((app.now-71*3600)*1000).toISOString()},caption_status:'was_live'}]});
    await app.toggle(true);
    await expect.poll(() => app.exists('classification-started')).toBe(true);
    await app.clock(app.now+2*3600);
    await app.write('classification-release');
    await expect.poll(async () => (await app.call('activity.snapshot')).expired).toBe(1);
    expect(await app.exists('caption-crossingE01')).toBe(false);
    expect(await app.exists('model-started')).toBe(false);
    await page.getByRole('region', {name:'处理队列',exact:true}).getByRole('button', {name:/^已过期/}).click();
    await expect(page.getByText('crossingE01', {exact:true})).toBeVisible();
  } finally {await app.close();}
});


for (const fault of ['network','rate']) test(`shared caption recovery ${fault} survives three retries and resumes without manual retry`,async({page,request})=>{
  test.setTimeout(90000);
  const now=Math.floor(Date.now()/1000);
  const scenario: Scenario={caption_failure:fault,retry_after:new Date((now+2400)*1000).toUTCString(),videos:[{id:'waiting-one',published:now-30},{id:'waiting-two',published:now-30}]};
  const app=await harness(page,request,scenario);
  const calls=async()=>{try{return (await readFile(join(app.root,'caption-requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));}catch{return [];}};
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await calls()).length).toBe(1);
    await page.waitForTimeout(1200);
    expect((await calls()).length).toBe(1);
    let deadline=(await calls())[0].at;
    for(const delay of fault === 'network' ? [600,1200,2400,21600] : [2400,1800,3600,21600]) {
      const activity=await app.call('activity.snapshot');
      expect(activity.youtube_requests.cooldown_until).toBe(deadline+delay);
      expect(activity.failed).toBe(0);
      deadline+=delay;
      const count=(await calls()).length;
      await app.clock(deadline);
      await expect.poll(async()=>(await calls()).length).toBe(count+1);
    }
    await app.stop(fault === 'network' ? 'SIGTERM' : 'SIGKILL');await app.start();
    const state=await app.call('activity.snapshot');
    expect(state.youtube_requests.cooldown_until).toBe(deadline+21600);
    await app.scenario({...scenario,caption_failure:undefined});
    await app.clock(deadline+21600);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:20000}).toBe(2);
  }finally{await app.close();}
});


test('confirmed caption absence yields and ends at 24 hours until explicit retry',async({page,request})=>{
  test.setTimeout(90000);
  const now=Math.floor(Date.now()/1000);
  const scenario: Scenario={caption_failure:'absent',videos:[{id:'absent-one',published:now-30},{id:'healthy-two',published:now-30}]};
  const app=await harness(page,request,scenario);
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
    const items=async()=>(await app.call('activity.list',{state:'waiting_retry'})).items;
    await expect.poll(async()=>(await items()).length).toBe(1);
    await page.getByRole('button',{name:/等待重试/}).click({timeout:5000});
    await expect(page.getByRole('region',{name:'处理队列',exact:true})).toContainText('下一次检查');
    const first=(await items())[0].retry_at-3600;
    expect(first).toBe(app.now);
    await app.clock(first+3600);
    await expect.poll(async()=>(await items())[0]?.retry_at).toBe(first+21600);
    await app.stop();await app.start();
    expect((await items())[0].retry_at).toBe(first+21600);
    await app.clock(first+21600);
    await expect.poll(async()=>(await items())[0]?.retry_at).toBe(first+86400);
    await app.clock(first+86400);
    await expect.poll(async()=>(await app.call('activity.snapshot')).failed).toBe(1);
    await app.scenario({...scenario,caption_failure:undefined});
    await app.clock(first+172800);
    await page.waitForTimeout(1200);
    expect((await app.call('activity.snapshot')).ready).toBe(1);
    await app.call('activity.retry',{video_id:'absent-one'});
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(2);
  }finally{await app.close();}
});


test('exhausted host batch resumes caption checks with official discovery blocked',async({page,request})=>{
  test.setTimeout(150000);
  const now=Math.floor(Date.now()/1000);
  const scenario: Scenario={caption_failure:'absent-all',channels:['alpha','beta'],
    videos:Array.from({length:100},(_,i)=>({id:`batch-${i}`,channel:i<50?'alpha':'beta',published:now-30}))};
  const app=await harness(page,request,scenario);
  try {
    await app.call('collection.refresh_updates');
    await app.scenario({...scenario,global_failure:'authorization'});
    await app.call('collection.refresh_updates');
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).waiting_retry,{timeout:90000}).toBe(100);
    expect((await readFile(join(app.root,'caption-requests.jsonl'),'utf8')).trim().split('\n')).toHaveLength(100);
    const count=(await app.requests()).length;
    await app.scenario({...scenario,caption_failure:undefined,global_failure:'authorization'});
    await app.clock(app.now+3600);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:12000}).toBeGreaterThan(0);
    expect((await app.requests()).length).toBe(count);
  }finally{await app.close();}
});


test('model account waiting yields to subtitles and preserves transcripts through restart',async({page,request})=>{
  test.setTimeout(60000);
  const now=Math.floor(Date.now()/1000);
  const scenario: Scenario={model_failure:true,videos:[{id:'model-one',published:now-30},{id:'model-two',published:now-30}]};
  const app=await harness(page,request,scenario);
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).waiting_retry).toBe(2);
    expect((await readFile(join(app.root,'model-requests.jsonl'),'utf8')).trim().split('\n')).toHaveLength(1);
    expect((await readFile(join(app.root,'caption-requests.jsonl'),'utf8')).trim().split('\n')).toHaveLength(2);
    await app.stop();await app.start();
    await app.call('activity.snapshot');
    await app.scenario({...scenario,model_failure:undefined,global_failure:'quota'});
    await app.clock(app.now+1200);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:12000}).toBe(2);
    expect((await readFile(join(app.root,'caption-requests.jsonl'),'utf8')).trim().split('\n')).toHaveLength(2);
  }finally{await app.close();}
});


test('unknown empty captions and access failures never become caption availability waiting',async({page,request})=>{
  test.setTimeout(45000);
  const now=Math.floor(Date.now()/1000);
  const scenario: Scenario={caption_failure:'terminal',videos:['private','deleted','denied','unknown'].map(id=>({id,published:now-30}))};
  const app=await harness(page,request,scenario);
  try {
    await app.toggle(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).unavailable).toBe(4);
    const items=(await app.call('activity.list',{state:'unavailable'})).items;
    expect(Object.fromEntries(items.map((item: any)=>[item.video_id,item.acquisition_failure_kind]))).toEqual({private:'private',deleted:'deleted',denied:'access_denied',unknown:'unknown'});
    expect((await app.call('activity.snapshot')).waiting_retry).toBe(0);
    await page.getByRole('button',{name:/暂不可用/}).click();
    await expect(page.getByRole('region',{name:'处理队列',exact:true})).toContainText('获取原因待核实');
  }finally{await app.close();}
});
