import { test, expect } from './fixtures';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

test('resource waiting preserves active manuscripts, yields to retained work and resumes only permitted requests', async ({page, request}) => {
  test.setTimeout(120000);
  const root = await mkdtemp(join(tmpdir(), 'edison-resources-'));
  let backend: ChildProcess | undefined;
  let url = '';
  let now = Math.floor(Date.now()/1000);
  const advance = async () => { now += 30; await writeFile(join(root, 'clock'), String(now)); };
  const resources = async (available: number, browser = 0) => writeFile(join(root,'resources.json'), JSON.stringify({available:available*1048576,browser_unreclaimable:browser*1048576,browser_swap:0,constrained:true}));
  const call = async (capability: string, args = {}) => {
    const result = await (await request.post(url+'/v1/youtube/capability', {headers:{'X-OpenWorker-Token':'content-e2e'},data:{capability,arguments:args}})).json();
    expect(result.ok, result.error).toBe(true); return result.result;
  };
  try {
    await advance(); await resources(1024);
    backend = spawn(process.env.EDISON_E2E_PYTHON || resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-content-server.py',root], {cwd:resolve('../..'),env:{...process.env,PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`},stdio:['ignore','pipe','pipe']});
    let logs = ''; backend.stderr!.on('data',chunk=>logs+=chunk);
    const port = await new Promise<number>((done,reject)=>{let output=''; const timer=setTimeout(()=>reject(new Error(logs||'backend timeout')),20000); backend!.stdout!.on('data',chunk=>{output+=chunk; const line=output.split('\n').find(x=>x.startsWith('{"port":')); if(line){clearTimeout(timer);done(JSON.parse(line).port);}});backend!.once('exit',()=>{clearTimeout(timer);reject(new Error(logs));});});
    url=`http://127.0.0.1:${port}`;
    await page.route('**/v1/youtube/capability',async route=>route.fulfill({response:await route.fetch({url:url+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'content-e2e'}})}));
    await page.addInitScript(()=>localStorage.setItem('openworker.lang','zh'));
    await page.goto('/'); await page.getByTestId('nav-youtube').click();
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog').getByRole('button',{name:'连接 YouTube',exact:true}).click();
    const content=page.getByRole('region',{name:'YouTube 内容会话'});
    await content.getByRole('button',{name:'打开登录画面',exact:true}).click();
    await expect(content.getByRole('status')).toHaveText('等待服务器资源');
    await expect(content.getByRole('alert')).toContainText('1.25 GiB');
    await resources(2048);
    await expect(content.getByAltText('服务器登录页面')).toBeVisible({timeout:10000});
    await content.getByAltText('服务器登录页面').click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await content.getByRole('button',{name:'完成登录并检查字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture',refresh_token:'fixture'});
    await writeFile(join(root,'model-hold'),'1');
    await advance();
    await call('manual.submit',{url:'https://youtu.be/one00000001',request_id:'resource-active'});
    await expect.poll(async()=>{await advance(); try{return await readFile(join(root,'model-active'),'utf8');}catch{return '';}},{timeout:20000}).toBe('active');
    await resources(300);
    await call('manual.submit',{url:'https://youtu.be/two00000002',request_id:'resource-wait'});
    await call('manual.submit',{url:'https://youtu.be/cancel00003',request_id:'resource-cancel'});
    await call('activity.cancel',{video_ids:['cancel00003']});
    await rm(join(root,'model-hold'));
    await expect.poll(async()=>(await call('library.inspect',{video_id:'one00000001'})).manuscript_version).toBe(1);
    expect((await call('activity.snapshot')).resources.captions_waiting).toBe(true);
    // A queued new-caption task must not occupy the serial worker ahead of retained work.
    await call('library.regenerate',{video_id:'one00000001'});
    await expect.poll(async()=>(await call('library.inspect',{video_id:'one00000001'})).manuscript_version).toBe(2);
    expect((await call('documents.get',{video_id:'one00000001'})).markdown).toBeTruthy();
    const records=()=>readFile(join(root,'caption-requests.jsonl'),'utf8');
    expect(await records()).not.toContain('two00000002');
    expect(await records()).not.toContain('cancel00003');
    await resources(2048); await advance();
    await expect.poll(async()=>{await advance(); return (await call('library.inspect',{video_id:'two00000002'})).manuscript_version;},{timeout:20000}).toBe(1);
    expect(await records()).not.toContain('cancel00003');
    expect((await call('activity.snapshot')).drain_paused).toBe(true);
    expect((await call('activity.snapshot')).cancelled).toBe(1);
    // Pressure changes after queue admission, while the external metadata response is in flight.
    await writeFile(join(root,'caption-metadata-hold'),'1'); await advance();
    await call('manual.submit',{url:'https://youtu.be/race0000004',request_id:'resource-race'});
    await expect.poll(async()=>{await advance(); try{return await readFile(join(root,'caption-metadata-active'),'utf8');}catch{return '';}},{timeout:20000}).toBe('race0000004');
    await resources(300); await rm(join(root,'caption-metadata-hold'));
    await expect.poll(async()=>(await call('library.inspect',{video_id:'race0000004'})).preparation_state).toBe('queued');
    expect((await call('activity.snapshot')).failed).toBe(0);
    await resources(2048); await advance();
    await expect.poll(async()=>{await advance(); return (await call('library.inspect',{video_id:'race0000004'})).manuscript_version;},{timeout:20000}).toBe(1);
    await content.getByRole('button',{name:'打开登录画面',exact:true}).click();
    await expect(content.getByAltText('服务器登录页面')).toBeVisible();
    await resources(800,1100);
    await expect(content.getByRole('status')).toHaveText('等待服务器资源');
    await expect(content.getByAltText('服务器登录页面')).toHaveCount(0);
    expect((await call('content_session.status')).snapshot_present).toBe(true);
    await content.getByRole('button',{name:'关闭服务器浏览器'}).click();
    await resources(2048); await advance();
    expect((await call('content_session.status')).browser_pending).toBe(false);
    expect((await call('content_session.status')).browser_running).toBe(false);


  } finally {
    await page.unroute('**/v1/youtube/capability');
    if(backend?.exitCode===null){ const ended=new Promise<void>(done=>backend!.once('exit',()=>done())); backend.kill('SIGTERM');await ended; }
    await rm(root,{recursive:true,force:true});
  }
});
