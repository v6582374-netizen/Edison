import {test, expect} from './fixtures';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile, readFile, rm, rename, access} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import type {Page, APIRequestContext} from '@playwright/test';

export type Scenario = {model_ready?: boolean; caption_failure?: boolean; hold_classification?: boolean; platform_pages?: boolean; channels?: string[]; videos?: {id: string; published: number; added?: number; channel?: string; kind?: string; claimed_publication?: number; live?: Record<string, string>; caption_status?: string; shorts?: boolean | null; listed?: boolean}[];
  global_failure?: string; failure_endpoint?: string; auth_failure?: boolean; channel_failure?: string; caption_cooldown?: boolean; more_pages?: boolean};
export async function harness(page: Page, request: APIRequestContext, scenario: Scenario, releaseModel = true, language = "zh") {
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
    backend = spawn(resolve('../../.venv/bin/python'), ['surfaces/gui/e2e/youtube-api-server.py',root],{cwd:resolve('../..'),env:{...process.env,EDISON_E2E_WEB:'1',PYTHONPATH:`${resolve('../..')}:${resolve('../../src')}`},stdio:['ignore','pipe','pipe']});
  let logs=''; backend.stderr!.on('data',chunk=>logs+=chunk);
  const port=await new Promise<number>((done,reject)=>{
    let output='';const timer=setTimeout(()=>reject(new Error(logs)),30000);
    backend.stdout!.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('{"port":'));if(line){clearTimeout(timer);done(JSON.parse(line).port);}});
    backend.once('exit',code=>{clearTimeout(timer);reject(new Error(`${code}: ${logs}`));});
  });
  url=`http://127.0.0.1:${port}`;
  await expect.poll(async()=>{try{return (await request.get(url+'/v1/health')).status();}catch{return 0;}}).toBe(200);
  };
  const stop = async () => {
    if (backend.exitCode === null) {
      const exited = new Promise<void>(done => backend.once('exit', () => done()));
      backend.kill('SIGKILL');
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
  await page.addInitScript(lang=>localStorage.setItem('openworker.lang',lang), language);
  await page.goto('/');await page.getByTestId('nav-youtube').click();
  const authorize=async()=>{
    await page.getByRole('button',{name:'订阅频道',exact:true}).click();
    await page.getByRole('dialog',{name:'订阅频道',exact:true}).getByRole('button',{name:'连接 YouTube',exact:true}).click();
    await page.getByRole('button',{name:'在浏览器中授权并导入订阅',exact:true}).click();
    await expect(page.getByText(/YouTube 已连接，已导入 \d+ 个订阅。/)).toBeVisible();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
  };
  await call('connection.import_authorization', {client_id:'fixture-client',client_secret:'fixture-client-secret',refresh_token:'fixture-refresh-secret'});
  await page.getByRole('button',{name:language === 'en' ? 'Progress' : '处理进度',exact:true}).click();
  const toggle=async(on: boolean)=>{
    await page.getByRole('button',{name:on?'恢复自动更新':'完成当前文档后暂停',exact:true}).click();
    await expect(page.getByRole('button',{name:on?'完成当前文档后暂停':'恢复自动更新',exact:true})).toBeEnabled();
  };
  return {root,now,write,exists,call,toggle,authorize,start,stop,address:()=>url,
    scenario:(value: Scenario)=>write('scenario.json',JSON.stringify(value)),
    clock:(value: number)=>write('clock',String(value)),
    requests:async()=>{try{return (await readFile(join(root,'api-requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));}catch{return []; }},
    close:async()=>{await stop();await rm(root,{recursive:true,force:true});},
  };
}
