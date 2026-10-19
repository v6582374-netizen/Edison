/** Opt-in Linux sandbox measurement. Never targets production or a real profile. */
import { test, expect } from './fixtures';
import { appendFile, writeFile } from 'node:fs/promises';
import type { Request } from '@playwright/test';

test('A-B-B-A native maintenance and reading under the same real preparation load', async ({page,request}) => {
  const server=process.env.EDISON_RESOURCE_MEASUREMENT_URL;
  test.skip(!server, 'Requires explicitly provisioned synthetic Linux fixture');
  test.setTimeout(1200000);
  const headers={'X-OpenWorker-Token':'content-e2e'};
  const output=process.env.EDISON_RESOURCE_MEASUREMENT_OUTPUT||'/tmp/edison-resource-measurement.json';
  const networkFailures:Array<Record<string,unknown>>=[];
  const journalWrites:Promise<void>[]=[];
  let phase='setup';let sample=-1;
  await writeFile(output+'.events.jsonl','');
  const record=(event:Record<string,unknown>)=>{
    const pending=appendFile(output+'.events.jsonl',JSON.stringify({at:Date.now(),phase,sample,...event})+'\n');
    journalWrites.push(pending);return pending;
  };
  const failed=(req:Request,status?:number)=>{
    if(req.url()!==server!+'/v1/youtube/capability')return;
    const failure={phase,sample,capability:req.method()==='POST'?req.postDataJSON()?.capability:'preflight',status,error:req.failure()?.errorText};
    networkFailures.push(failure);void record({kind:'network_failure',...failure});
  };
  page.on('requestfailed',req=>failed(req));
  page.on('response',response=>{if(response.status()>=400)failed(response.request(),response.status());});
  const call=async(capability:string,args={})=>{const payload=await(await request.post(server!+'/v1/youtube/capability',{headers,data:{capability,arguments:args}})).json();expect(payload.ok,payload.error).toBe(true);return payload.result;};
  const native=async(operation:string)=>{const response=await request.post(server!+'/_measurement/native',{headers,data:{operation}});expect(response.ok()).toBe(true);return response.json();};
  // Browser-native transport includes the real API token and CORS path. It
  // bypasses only the generic UI fixture route, with no Node HTTP forwarding.
  await page.route('**/v1/youtube/capability',route=>route.continue());
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
  const signature='Readers can compare the source with the translation and return to their saved place.';
  await call('manual.submit',{url:'https://youtu.be/load0000001',request_id:'measurement-background'});
  await expect.poll(async()=>(await call('library.inspect',{video_id:'load0000001'})).manuscript_version,{timeout:60000}).toBe(1);
  const blocks: Array<Record<string,unknown>>=[];
  let screenshots: ReturnType<typeof setInterval>|undefined;
  let screenPoll: Promise<void>|undefined;
  let screenFailure='';
  let filling=true;
  let loadIndex=0;
  const loadErrors:string[]=[];
  // Repeated explicit regeneration of one retained transcript: the same input
  // and real translation/checkpoint/composition/persistence work in every arm.
  const load=(async()=>{while(filling){
    try {const asset=await call('library.inspect',{video_id:'load0000001'});if(asset.preparation_state==='ready'){
      await call('library.regenerate',{video_id:'load0000001'});loadIndex++;
    }} catch(error){loadErrors.push(String(error));}
    await new Promise(resolve=>setTimeout(resolve,250));
  }})();
  try {
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
      await writeFile(output,JSON.stringify({complete:false,transport:'browser-direct',blocks,networkFailures,documentCharacters:document.markdown.length,loadIndex,loadErrors},null,2));
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
    const summary={baselineSamples:baseline.length,maintenanceSamples:maintenance.length,baselineP95:p95(baseline),maintenanceP95:p95(maintenance),allowedIncrease:Math.max(.2*p95(baseline),200),increase:p95(maintenance)-p95(baseline)};
    await writeFile(output,JSON.stringify({complete:true,transport:'browser-direct',blocks,summary,networkFailures,documentCharacters:document.markdown.length,loadIndex,loadErrors},null,2));
    expect(networkFailures).toEqual([]);
    expect(loadErrors).toEqual([]);
    expect(summary.increase).toBeLessThanOrEqual(summary.allowedIncrease);
  } catch(error) {
    await writeFile(output,JSON.stringify({complete:false,failure:String(error).split('\n')[0],phase,sample,transport:'browser-direct',blocks,networkFailures,documentCharacters:document.markdown.length,loadIndex,loadErrors},null,2));
    throw error;
  } finally {
    try {
      filling=false;await load;if(screenshots)clearInterval(screenshots);await screenPoll;await native('close');
      await page.unroute('**/v1/youtube/capability');
      await Promise.all(journalWrites);
      expect(networkFailures).toEqual([]);
      expect(loadErrors).toEqual([]);
    } catch(error) {
      await writeFile(output,JSON.stringify({complete:false,failure:String(error).split('\n')[0],phase:'cleanup',transport:'browser-direct',blocks,networkFailures,documentCharacters:document.markdown.length,loadIndex,loadErrors},null,2));
      throw error;
    }
  }
});
