/** Opt-in Linux sandbox measurement. Never targets production or a real profile. */
import { test, expect } from './fixtures';
import { appendFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import type { Request, Response } from '@playwright/test';

test('A-B-B-A native maintenance and reading under the same real preparation load', async ({page}) => {
  const server=process.env.EDISON_RESOURCE_MEASUREMENT_URL;
  test.skip(!server, 'Requires explicitly provisioned synthetic Linux fixture');
  test.setTimeout(1200000);
  const output=process.env.EDISON_RESOURCE_MEASUREMENT_OUTPUT||'/tmp/edison-resource-measurement.json';
  const runId=randomUUID();
  await writeFile(output,JSON.stringify({runId,complete:false,phase:'setup',blocks:[]},null,2));
  const networkFailures:Array<Record<string,unknown>>=[];
  const journalWrites:Promise<void>[]=[];
  let phase='setup';let sample=-1;
  await writeFile(output+'.events.jsonl','');
  const record=(event:Record<string,unknown>)=>{
    const pending=appendFile(output+'.events.jsonl',JSON.stringify({runId,at:Date.now(),phase,sample,...event})+'\n');
    journalWrites.push(pending);return pending;
  };
  const failed=(req:Request,status?:number)=>{
    if(![server!+'/v1/youtube/capability',server!+'/_measurement/native'].includes(req.url()))return;
    const payload=req.method()==='POST'?req.postDataJSON():null;
    const failure={phase,sample,capability:payload?.capability,operation:payload?.operation,method:req.method(),status,error:req.failure()?.errorText};
    networkFailures.push(failure);void record({kind:'network_failure',...failure});
  };
  const requestFailed=(req:Request)=>failed(req);
  const responseReceived=(response:Response)=>{if(response.status()>=400)failed(response.request(),response.status());};
  page.on('requestfailed',requestFailed);
  page.on('response',responseReceived);
  const post=async(path:string,data:Record<string,unknown>)=>{
    try {
      return await page.evaluate(async({url,data})=>{
        const started=performance.now();
        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-OpenWorker-Token':'content-e2e'},body:JSON.stringify(data)});
        if(!response.ok)throw new Error(`Measurement HTTP ${response.status}`);
        let value=await response.json();
        const networkMs=performance.now()-started;
        let decodeMs=0;
        if(data.operation==='screen'){
          if(typeof value.image!=='string'||!value.image)throw new Error('Native screen image missing');
          const image=new Image();
          const decodeStarted=performance.now();
          try {
            image.src='data:image/png;base64,'+value.image;await image.decode();
            if(image.naturalWidth!==value.width||image.naturalHeight!==value.height)throw new Error('Native screen dimensions mismatch');
            value={width:value.width,height:value.height,encodedCharacters:value.image.length,pngBytes:atob(value.image).length};
          } finally {image.src='';}
          decodeMs=performance.now()-decodeStarted;
        }
        return {value,networkMs,decodeMs,totalMs:performance.now()-started};
      },{url:server!+path,data});
    } catch(error) {
      await record({kind:'http_failure',capability:data.capability,operation:data.operation,error:String(error).split('\n')[0]});
      throw error;
    }
  };
  const call=async(capability:string,args={})=>{const {value}=await post('/v1/youtube/capability',{capability,arguments:args});expect(value.ok,value.error).toBe(true);return value.result;};
  let nativeTouched=false;
  let previousScreenStart:number|undefined;
  const nativeCalls:Array<Record<string,unknown>>=[];
  const native=async(operation:string)=>{
    nativeTouched=true;
    const started=Date.now();
    const event:Record<string,unknown>={at:started,phase,sample,operation};
    if(operation==='open')previousScreenStart=undefined;
    if(operation==='screen'){
      event.startIntervalMs=previousScreenStart===undefined?null:started-previousScreenStart;
      previousScreenStart=started;
    }
    try {
      const response=await post('/_measurement/native',{operation});
      Object.assign(event,{ok:true,networkMs:response.networkMs,decodeMs:response.decodeMs,totalMs:response.totalMs});
      if(operation==='screen')Object.assign(event,response.value);
      return response.value;
    } catch(error) {
      Object.assign(event,{ok:false,error:String(error).split('\n')[0]});throw error;
    } finally {
      event.elapsedMs=Date.now()-started;nativeCalls.push(event);await record({kind:'native_operation',...event});
    }
  };
  const blocks: Array<Record<string,unknown>>=[];
  let screenshots: ReturnType<typeof setInterval>|undefined;
  let screenPoll: Promise<void>|undefined;
  let screenFailure='';
  let filling=false;
  let load:Promise<void>=Promise.resolve();
  let documentCharacters=0;
  let summary:Record<string,number>|undefined;
  let primaryError:unknown;
  const cleanupErrors:string[]=[];
  let loadIndex=0;
  const loadErrors:string[]=[];
  try {
  // Browser-native transport includes the real API token and CORS path. It
  // bypasses only the generic UI fixture route, with no Node HTTP forwarding.
  await page.route('**/v1/youtube/capability',route=>route.continue());
  await page.route('**/_measurement/native',route=>route.continue());
  await page.addInitScript(({server})=>{
    Object.assign(globalThis,{__COWORKER_HTTP__:server,__COWORKER_WS__:server.replace(/^http/,'ws'),__COWORKER_API_TOKEN__:'content-e2e'});
    localStorage.setItem('openworker.lang','zh');
  },{server:server!});
  await page.goto('/');await page.getByTestId('nav-youtube').click();
  // Synthetic external platform context for the real host preparation pipeline.
  // This Browser fixture is not the separately measured actual native browser.
  await call('content_session.open'); await call('content_session.click',{x:1,y:1});
  await call('content_session.confirm',{video_id:'abcdefghijk',same_account:true});
  await call('connection.import_authorization',{client_id:'fixture',client_secret:'fixture',refresh_token:'fixture'});
  await call('manual.submit',{url:'https://youtu.be/read0000001',request_id:'measurement-reader'});
  await expect.poll(async()=>(await call('library.inspect',{video_id:'read0000001'})).manuscript_version,{timeout:60000}).toBe(1);
  const document=await call('documents.get',{video_id:'read0000001'});
  documentCharacters=document.markdown.length;
  const signature='Readers can compare the source with the translation and return to their saved place.';
  await call('manual.submit',{url:'https://youtu.be/load0000001',request_id:'measurement-background'});
  await expect.poll(async()=>(await call('library.inspect',{video_id:'load0000001'})).manuscript_version,{timeout:60000}).toBe(1);

  filling=true;
  // Repeated explicit regeneration of one retained transcript: the same input
  // and real translation/checkpoint/composition/persistence work in every arm.
  load=(async()=>{while(filling){
    try {const asset=await call('library.inspect',{video_id:'load0000001'});if(asset.preparation_state==='ready'){
      await call('library.regenerate',{video_id:'load0000001'});loadIndex++;
    }} catch(error){loadErrors.push(String(error));}
    await new Promise(resolve=>setTimeout(resolve,250));
  }})();
    for(const [block,maintenance] of [false,true,true,false].entries()){
      phase=`${block}-${maintenance?'maintenance':'baseline'}`;sample=-1;
      expect(networkFailures).toEqual([]);
      if(screenshots)clearInterval(screenshots);
      await screenPoll;
      await native('close');
      const started=Date.now();
      if(maintenance)await native('open');
      const openMs=Date.now()-started;
      let sampling=false;
      if(maintenance)screenshots=setInterval(()=>{if(!sampling){sampling=true;screenPoll=native('screen').then(()=>{}).catch(error=>screenFailure=String(error)).finally(()=>sampling=false);}},1200);
      const before={activity:await call('activity.snapshot'),native:await native('status'),load:await call('library.inspect',{video_id:'load0000001'})};
      const samples:number[]=[];
      const admission=[];
      let documentRequests=0;
      const observer=(req:{url():string;postDataJSON():{capability?:string}})=>{if(req.url().includes('/v1/youtube/capability')&&req.postDataJSON()?.capability==='documents.get')documentRequests++;};
      page.on('request',observer);
      const current:Record<string,unknown>={block,maintenance,samples,openMs,before,admission,complete:false};
      blocks.push(current);
      await record({kind:'block_start',block,maintenance,before});
      for(let index=0;index<60;index++){
        sample=index;expect(networkFailures).toEqual([]);
        const previousRequests=documentRequests;
        const back=page.getByRole('button',{name:'返回文档库',exact:true});if(await back.isVisible())await back.click();
        await page.getByRole('button',{name:'添加视频',exact:true}).click();
        await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/read0000001');
        const elapsed=await page.evaluate(({signature})=>new Promise<number>((resolve,reject)=>{
          const started=performance.now();
          const timer=setTimeout(()=>{observer.disconnect();reject(new Error('reader render timeout'));},15000);
          const check=()=>{
            const article=document.querySelector('.yp-requested-document');
            if(article?.textContent?.includes(signature)){
              observer.disconnect();requestAnimationFrame(()=>requestAnimationFrame(()=>{clearTimeout(timer);resolve(performance.now()-started);}));
            }
          };
          const observer=new MutationObserver(check);observer.observe(document.body,{childList:true,subtree:true,characterData:true});
          const button=Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='添加并处理');
          if(!button)throw new Error('submit button missing');button.click();check();
        }),{signature});
        const requests=documentRequests-previousRequests;
        await record({kind:'render',elapsed,warmup:index<10,documentRequests:requests});
        expect(networkFailures).toEqual([]);
        expect(requests).toBeGreaterThanOrEqual(1);
        if(index>=10)samples.push(elapsed);
        if(maintenance && index%10===0)admission.push(await native('admission'));
      }
      page.off('request',observer);
      const after={activity:await call('activity.snapshot'),native:await native('status'),load:await call('library.inspect',{video_id:'load0000001'})};
      Object.assign(current,{documentRequests,after,screenFailure,elapsedMs:Date.now()-started,complete:true});
      await writeFile(output,JSON.stringify({runId,complete:false,transport:'browser-direct-all-http-v3',blocks,nativeCalls,networkFailures,documentCharacters,loadIndex,loadErrors},null,2));
      // React's development StrictMode can issue two effect reads per mount;
      // each opening above must still perform a fresh request. Report the count.
      expect(documentRequests).toBeGreaterThanOrEqual(60);
      expect(after.native.model_calls).toBeGreaterThan(before.native.model_calls);
      expect(after.load.manuscript_version).toBeGreaterThan(before.load.manuscript_version);
      expect(screenFailure).toBe('');
    }
    const p95=(values:number[])=>[...values].sort((a,b)=>a-b)[Math.ceil(.95*values.length)-1];
    const baseline=blocks.filter(block=>!block.maintenance).flatMap(block=>block.samples as number[]);
    const maintenance=blocks.filter(block=>block.maintenance).flatMap(block=>block.samples as number[]);
    summary={baselineSamples:baseline.length,maintenanceSamples:maintenance.length,baselineP95:p95(baseline),maintenanceP95:p95(maintenance),allowedIncrease:Math.max(.2*p95(baseline),200),increase:p95(maintenance)-p95(baseline)};
    expect(networkFailures).toEqual([]);
    expect(loadErrors).toEqual([]);
    expect(summary.increase).toBeLessThanOrEqual(summary.allowedIncrease);
  } catch(error) {
    primaryError=error;
  } finally {
    filling=false;if(screenshots)clearInterval(screenshots);
    const cleanup=async(action:()=>Promise<unknown>)=>{try{await action();}catch(error){cleanupErrors.push(String(error).split('\n')[0]);}};
    await cleanup(()=>load);
    await cleanup(async()=>screenPoll);
    if(nativeTouched)await cleanup(()=>native('close'));
    await cleanup(()=>page.unroute('**/v1/youtube/capability'));
    await cleanup(()=>page.unroute('**/_measurement/native'));
    page.off('requestfailed',requestFailed);page.off('response',responseReceived);
    await cleanup(()=>Promise.all(journalWrites));
    const complete=!primaryError && !cleanupErrors.length && !networkFailures.length && !loadErrors.length && !!summary;
    try {
      await writeFile(output,JSON.stringify({runId,complete,failure:primaryError?String(primaryError).split('\n')[0]:undefined,
        phase,sample,cleanupErrors,transport:'browser-direct-all-http-v3',blocks,summary:complete?summary:undefined,
        nativeCalls,networkFailures,documentCharacters,loadIndex,loadErrors},null,2));
    } catch(error) {
      // The early incomplete marker remains; preserve the initiating failure.
      if(!primaryError)primaryError=error;
    }
  }
  if(primaryError)throw primaryError;
  expect(cleanupErrors).toEqual([]);
  expect(networkFailures).toEqual([]);
  expect(loadErrors).toEqual([]);
});
