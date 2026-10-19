import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('unified withdrawal preserves reading, cancellation and permission through reconnect and restart', async ({ page, request }) => {
  test.setTimeout(120000);
  const root = await mkdtemp(join(tmpdir(), 'edison-disconnect-'));
  let backend: ChildProcess;
  let url = '';
  let now = Math.floor(Date.now()/1000);
  const advance = async (seconds=20) => { now += seconds; await writeFile(join(root,'clock'),String(now)); };
  async function start() {
    backend = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py',root], {
      cwd: resolve('../..'), env:{...process.env,PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`}, stdio:['ignore','pipe','pipe'],
    });
    let logs=''; backend.stderr!.on('data',data=>logs+=data);
    const port=await new Promise<number>((done,reject)=>{
      let output=''; const timer=setTimeout(()=>reject(new Error(logs||'backend timeout')),20000);
      backend.stdout!.on('data',data=>{output+=data; const line=output.split('\n').find(line=>line.startsWith('{"port":')); if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
      backend.once('exit',()=>{clearTimeout(timer);reject(new Error(logs));});
    });
    url=`http://127.0.0.1:${port}`;
    await expect.poll(async()=>{try{return(await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  }
  async function stop(){if(backend?.exitCode===null){const ended=new Promise<void>(done=>backend.once('exit',()=>done()));backend.kill('SIGTERM');await ended;}}
  const raw = async(capability:string,args={})=>(await request.post(url+'/v1/youtube/capability',{headers:{'X-OpenWorker-Token':'content-e2e'},data:{capability,arguments:args}})).json();
  const call = async(capability:string,args={})=>{const value=await raw(capability,args);expect(value.ok,value.error).toBe(true);return value.result;};
  async function panel(){await page.getByRole('button',{name:'订阅频道',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'连接 YouTube',exact:true}).click();}
  const content=page.getByRole('region',{name:'YouTube 内容会话'});
  const official=page.getByRole('region',{name:'Google 官方授权'});
  async function authorize(){
    await official.getByText('首次连接或重新授权',{exact:true}).click();
    await official.getByLabel('官方授权交接文件').setInputFiles({name:'handoff.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({client_id:'fixture',client_secret:'fixture-secret',refresh_token:'fixture-refresh'}))});
    await expect(official.getByRole('status')).toHaveText('订阅能力已验证');
  }
  async function login(){
    await content.getByRole('button',{name:'打开登录画面',exact:true}).click();
    await content.getByAltText('服务器登录页面').click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await advance();
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
  }
  try {
    await advance(0);await start();
    await page.route('**/v1/youtube/capability',async route=>{
      const response=await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'content-e2e'}});
      await route.fulfill({response});
    });
    await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
    await page.goto('/');await page.getByTestId('nav-youtube').click();await panel();
    await authorize();await login();
    await call('manual.submit',{url:'https://youtu.be/mnopqrstuvw',request_id:'kept-document'});
    await expect.poll(async()=>{await advance(3);return(await call('library.list')).assets.some((a:{video_id:string;manuscript_version:number|null})=>a.video_id==='mnopqrstuvw'&&a.manuscript_version!==null);},{timeout:30000}).toBe(true);
    await call('library.set_reading_state',{video_id:'mnopqrstuvw',reading_state:'read'});
    const kept=await call('library.inspect',{video_id:'mnopqrstuvw'});
    // External model availability prevents commencement; the real host loop remains running.
    await writeFile(join(root,'model-unavailable'),'');
    await call('manual.submit',{url:'https://youtu.be/zyxwvutsrqp',request_id:'cancelled-video'});
    await call('activity.cancel',{video_ids:['zyxwvutsrqp']});
    await rm(join(root,'model-unavailable'));
    await writeFile(join(root,'revoke-fails'),'');
    await expect(page.getByRole('button',{name:'断开 YouTube',exact:true})).toBeVisible({timeout:5000});
    await page.getByRole('button',{name:'断开 YouTube',exact:true}).click();
    await expect(page.getByText('本实例已清除 Google 官方授权与 YouTube 内容会话。',{exact:true})).toBeVisible();
    await expect(page.getByText('Google 平台撤销失败；本实例已停止使用，已有资料仍可阅读。',{exact:true})).toBeVisible();
    await expect(content.getByRole('status')).toHaveText('内容会话已断开');
    for(const capability of ['session_cookies.replace','connection.migrate_credentials','connection.configure','connection.authorize']){
      const response=await raw(capability,{text:'obsolete-secret',client_id:'obsolete',client_secret:'obsolete'});
      expect(response.kind).toBe('retired_entry');expect(response.error).toContain('刷新页面');
    }
    expect((await call('library.inspect',{video_id:'mnopqrstuvw'}))).toEqual(kept);
    expect((await call('activity.snapshot')).cancelled).toBe(1);
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    expect(await readFile(join(root,'credentials.json'),'utf8')).not.toContain('fixture-refresh');
    await page.reload();await page.getByTestId('nav-youtube').click();await panel();
    await expect(content.getByRole('status')).toHaveText('内容会话已断开');
    await authorize();await login();
    expect((await call('activity.snapshot')).cancelled).toBe(1);
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    expect((await call('library.inspect',{video_id:'mnopqrstuvw'}))).toEqual(kept);
    // Two real requests are already across external boundaries when withdrawal
    // begins. Their late replies must not restore either connection.
    await writeFile(join(root,'hold-subscriptions'),'');
    const oldOfficial = page.waitForResponse(response => response.url().includes('/v1/youtube/capability') && response.request().postDataJSON()?.capability === 'connection.refresh_subscriptions');
    await official.getByRole('button',{name:'检查订阅能力'}).click();
    await expect.poll(async()=>{try{await readFile(join(root,'subscriptions-held'));return true;}catch{return false;}}).toBe(true);
    await advance();await writeFile(join(root,'caption-mode'),'hold');
    const oldCaption = page.waitForResponse(response => response.url().includes('/v1/youtube/capability') && response.request().postDataJSON()?.capability === 'content_session.verify');
    await content.getByRole('button',{name:'重新实际获取字幕'}).click();
    await expect.poll(async()=>{try{return Number(await readFile(join(root,'caption-held'),'utf8'));}catch{return 0;}}).toBeGreaterThan(0);
    const captionPid=Number(await readFile(join(root,'caption-held'),'utf8'));
    await rm(join(root,'revoke-fails'));
    await page.getByRole('button',{name:'断开 YouTube',exact:true}).click();
    await expect(page.getByText('Google 平台授权已撤销。',{exact:true})).toBeVisible();
    await rm(join(root,'hold-subscriptions'));
    expect((await (await oldOfficial).json()).ok).toBe(false);
    expect((await (await oldCaption).json()).ok).toBe(false);
    await expect.poll(()=>{try{process.kill(captionPid,0);return true;}catch{return false;}}).toBe(false);
    expect((await call('connection.status')).authorized).toBe(false);
    await expect(content.getByRole('status')).toHaveText('内容会话已断开');
    await page.unrouteAll({behavior:'wait'});await stop();await start();
    expect((await call('connection.status')).authorized).toBe(false);
    expect((await call('content_session.status')).state).toBe('disconnected');
    expect((await call('library.inspect',{video_id:'mnopqrstuvw'}))).toEqual(kept);
    expect((await call('activity.snapshot')).cancelled).toBe(1);
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
  } finally {try {await page.unrouteAll({behavior:'wait'});} finally {await stop();await rm(root,{recursive:true,force:true});}}
});
