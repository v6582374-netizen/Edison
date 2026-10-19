import { test, expect } from './fixtures';
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('openworker.lang', 'zh'));
});

test('YouTube progress is a page, not an overflowing popover', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 850 });
  await page.route('**/v1/youtube/capability', async route => {
    const { capability } = route.request().postDataJSON();
    let result: unknown = {};
    if (capability === 'library.list') result = { assets: [], total: 0 };
    if (capability === 'collection.preferences') result = { sources: [{channel_id: 'one', title: 'Example'}], excluded_channels: [] };
    if (capability === 'activity.list') result = {items: [], total: 0};
    if (capability === 'activity.snapshot') result = {
      queued: 24, generating: 1, acquiring: 0, ready: 13, failed: 1, unavailable: 9,
      drain_paused: false, model_ready: true, batch: { completed: 13, limit: 100 },
      discovery_error: '暂时无法刷新订阅，稍后自动重试。',
      failures: Array.from({length: 10}, (_, i) => ({video_id: String(i), title: `Yohji Yamamoto ${i}`, state: 'unavailable', reason: 'HTTP Error 429: Too Many Requests'})),
    };
    await route.fulfill({ json: {ok: true, result} });
  });
  await page.goto('/');
  await page.getByTestId('nav-youtube').click();
  await expect(page.getByRole('button', { name: '自动更新中', exact: true })).toHaveCount(0);
  const status = page.getByRole('status').filter({ hasText: '自动更新中' });
  await expect(status).toBeVisible();
  await status.click();
  await expect(page.getByRole('button', { name: '处理进度', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: '处理进度', exact: true }).click();
  await page.screenshot({ path: 'test-results/youtube-progress.png', fullPage: true });
  await expect(page.locator('.yp-popover')).toHaveCount(0);
  await expect(page.getByRole('heading', {name: 'YouTube', exact: true})).toHaveCount(0);
  await expect(page.getByRole('region', {name: '处理进度'})).toBeVisible();
  await expect(page.getByRole('heading', {name: '处理进度', exact: true})).toHaveCount(0);
  await expect(page.getByText('从订阅更新，到可以阅读的文档。')).toHaveCount(0);
  await expect(page.locator('.yp-content .yp-progress-notice')).toHaveCount(0);
  await expect(page.locator('.notice-card')).toHaveText('暂时无法刷新订阅，稍后自动重试。');
  await page.getByRole('button', {name: '关闭通知'}).click();
  await page.waitForTimeout(4500);
  await expect(page.locator('.notice-card')).toHaveCount(0);
});

test('failure explanations are distinct and the complete list is paginated', async ({page}) => {
  const unavailable = Array.from({length: 52}, (_, i) => ({video_id: `video-${i}`, title: `视频 ${i}`, channel_title: 'Example', preparation_state: 'unavailable', failure_reason: i === 0 ? 'HTTP Error 429: Too Many Requests' : 'no subtitles were available', manuscript_version: null}));
  const failed = [{video_id: 'interrupted', title: '中断的视频', channel_title: 'Example', preparation_state: 'failed', failure_reason: '上次处理被中断，已完成阶段保留，可重试。', manuscript_version: null}];
  await page.route('**/v1/youtube/capability', async route => {
    const {capability, arguments: args = {}} = route.request().postDataJSON();
    let result: unknown = {};
    if (capability === 'collection.preferences') result = {sources: [], excluded_channels: []};
    if (capability === 'library.list') result = {assets: []};
    if (capability === 'activity.snapshot') result = {queued: 0, ready: 0, acquiring: 0, generating: 0, failed: failed.length, unavailable: unavailable.length, drain_paused: false, model_ready: true, batch: {completed: 0, limit: 100}, failures: []};
    if (capability === 'activity.list') {
      const items = args.state === 'unavailable' ? unavailable : args.state === 'failed' ? failed : [];
      result = {items: items.slice(args.offset, args.offset + args.limit), total: items.length};
    }
    if (capability === 'activity.retry') { expect(args.video_id).toBe('interrupted'); failed.length = 0; result = {queued: 1}; }
    await route.fulfill({json: {ok: true, result}});
  });
  await page.goto('/');
  await page.getByTestId('nav-youtube').click();
  await page.getByRole('button', {name: '处理进度', exact: true}).click();
  const queue = page.getByRole('region', {name: '处理队列'});
  await queue.getByRole('button', {name: /^暂不可用/}).click();
  await expect(queue.locator('li')).toHaveCount(50);
  const limited = queue.locator('li').filter({hasText: 'YouTube 请求限流'});
  await expect(limited).toBeVisible();
  await expect(limited.locator('pre')).not.toBeVisible();
  await limited.getByText('技术详情', {exact: true}).click();
  await expect(limited.locator('pre')).toContainText('429');
  await expect(queue.getByText(/获取原因待核实/).first()).toBeVisible();
  await queue.getByRole('button', {name: '下一页'}).click();
  await expect(queue.locator('li')).toHaveCount(2);
  await expect(queue.getByText('视频 51', {exact: true})).toBeVisible();
  await queue.getByRole('button', {name: /^处理失败/}).click();
  await expect(queue.getByText(/上次处理被中断/).first()).toBeVisible();
  await queue.getByRole('button', {name: '继续重试'}).click();
  await expect(queue.getByText('没有处理失败的视频。')).toBeVisible();
});
