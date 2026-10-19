import { test, expect } from './fixtures';
import { harness } from './youtube-manual-harness';

test('manual old outside-subscription video runs with Auto Update off and duplicate opens existing document', async ({ page, request }) => {
  const app = await harness(page, request, {channels:[],videos:[{id:'abcdefghijk',published:1577836800,channel:'outsider'}]});
  try {
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk?t=90');
    await page.getByRole('button',{name:'添加并处理'}).click();
    const target = page.getByRole('region',{name:'已接收的视频'});
    await expect(target.getByRole('heading',{name:'abcdefghijk'})).toBeVisible();
    await expect(target.getByRole('button',{name:'打开现有文档'})).toBeVisible({timeout:20000});
    expect((await app.call('activity.snapshot')).drain_paused).toBe(true);
    expect((await app.call('collection.preferences')).sources).toEqual([]);
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://www.youtube.com/watch?v=abcdefghijk&list=PLtest');
    await page.getByLabel('视频链接',{exact:true}).press('Enter');
    await expect(page.getByRole('article',{name:'abcdefghijk'})).toBeVisible();
    expect((await app.call('library.inspect',{video_id:'abcdefghijk'})).generation_records).toHaveLength(1);
  } finally { await app.close(); }
});

test('saved response loss is confirmed and not submitted twice', async ({page,request}) => {
  const app = await harness(page,request,{channels:[],videos:[{id:'abcdefghijk',published:1577836800}]});
  let lost = false;
  try {
    await page.route('**/v1/youtube/capability', async route => {
      if (route.request().postDataJSON().capability === 'manual.submit' && !lost) {
        lost = true;
        await route.fetch({url:app.address()+'/v1/youtube/capability',headers:{...route.request().headers(),'X-OpenWorker-Token':'youtube-api-e2e'}});
        await route.abort();
      } else await route.fallback();
    });
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect(page.getByRole('region',{name:'已接收的视频'})).toBeVisible();
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
    expect((await app.call('library.inspect',{video_id:'abcdefghijk'})).generation_records).toHaveLength(1);
    expect(lost).toBe(true);
  } finally {await app.close();}
});

test('invalid or unreachable new video preserves input and never imports automatically', async ({page,request}) => {
  const scenario={channels:[],videos:[{id:'abcdefghijk',published:1577836800}],global_failure:'service',failure_endpoint:'videos'};
  const app=await harness(page,request,scenario);
  try {
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    const input=page.getByLabel('视频链接',{exact:true});
    await input.fill('https://youtube.com/playlist?list=PLtest'); await input.press('Enter');
    await expect(page.getByRole('status').filter({hasText:'不支持频道或纯播放列表'})).toBeVisible();
    await input.fill('https://youtu.be/abcdefghijk'); await input.press('Enter');
    await expect(page.getByRole('status').filter({hasText:'尚未添加。'})).toBeVisible();
    await expect(input).toHaveValue('https://youtu.be/abcdefghijk');
    await app.scenario({...scenario,global_failure:undefined}); await app.clock(app.now+65);
    expect((await app.call('library.list')).total).toBe(0);
    await expect(input).toHaveValue('https://youtu.be/abcdefghijk');
    await input.press('Enter');
    await expect(page.getByRole('region',{name:'已接收的视频'})).toBeVisible();
  } finally {await app.close();}
});

test('cancellation survives repost and crash; explicit manual restoration resumes while Auto Update stays off', async({page,request})=>{
  const scenario={channels:[],videos:[{id:'abcdefghijk',published:1577836800}],model_ready:false};
  const app=await harness(page,request,scenario);
  try {
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect(page.getByRole('region',{name:'已接收的视频'})).toBeVisible();
    await page.getByRole('button',{name:'取消处理',exact:true}).click();
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect(page.getByRole('button',{name:'恢复此视频处理'})).toBeVisible();
    await app.stop(); await app.start();
    await page.getByRole('button',{name:'恢复此视频处理'}).click();
    await expect(page.getByRole('region',{name:'已接收的视频'})).toBeVisible();
    await app.stop(); await app.scenario({...scenario,model_ready:true}); await app.start();
    await expect.poll(()=>app.exists('caption-abcdefghijk'),{timeout:15000}).toBe(true);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
    expect((await app.call('activity.snapshot')).drain_paused).toBe(true);
  } finally {await app.close();}
});

test('unavailable video requires explicit retry and retained manuscript is not overwritten',async({page,request})=>{
  const scenario={channels:[],videos:[{id:'abcdefghijk',published:1577836800}],caption_failure:true};
  const app=await harness(page,request,scenario);
  try {
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect.poll(async()=>(await app.call('activity.snapshot')).unavailable).toBe(1);
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtu.be/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect(page.getByRole('button',{name:'重试此视频'})).toBeVisible();
    await app.scenario({...scenario,caption_failure:false});
    await page.getByRole('button',{name:'重试此视频'}).click();
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready).toBe(1);
  } finally {await app.close();}
});

test('manual live video is checked after ending even outside subscriptions with Auto Update off',async({page,request})=>{
  const now=Math.floor(Date.now()/1000);
  const waiting={id:'abcdefghijk',channel:'outsider',published:1577836800,kind:'live',live:{actualStartTime:new Date((now-100)*1000).toISOString()},caption_status:'was_live'};
  const app=await harness(page,request,{channels:[],videos:[waiting]});
  try {
    await page.getByRole('button',{name:'添加视频',exact:true}).click();
    await page.getByLabel('视频链接',{exact:true}).fill('https://youtube.com/live/abcdefghijk');
    await page.getByRole('button',{name:'添加并处理'}).click();
    await expect(page.getByRole('region',{name:'已接收的视频'})).toBeVisible();
    await expect.poll(async()=>(await app.call('library.inspect',{video_id:'abcdefghijk'})).preparation_state).toBe('awaiting_completion');
    expect(await app.exists('caption-abcdefghijk')).toBe(false);
    await app.scenario({channels:[],videos:[{...waiting,kind:'none',live:{...waiting.live,actualEndTime:new Date((now+100)*1000).toISOString()}}]});
    await app.clock(now+301);
    await expect.poll(async()=>(await app.call('activity.snapshot')).ready,{timeout:8000}).toBe(1);
    expect((await app.call('activity.snapshot')).drain_paused).toBe(true);
  }finally{await app.close();}
});
