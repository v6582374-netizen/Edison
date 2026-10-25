import {test, expect} from './fixtures';
import type {Page} from '@playwright/test';

async function runtime(page: Page, language = 'zh', notice: Record<string, unknown> = {level: 'none'}) {
  await page.addInitScript(lang => {localStorage.setItem('openworker.lang', lang); localStorage.setItem('openwork-theme', 'light');}, language);
  const requests: string[] = [];
  await page.route('**/v1/youtube/capability', async route => {
    const {capability} = route.request().postDataJSON();
    requests.push(capability);
    const now = Date.now() / 1000;
    const available = {state: 'available', last_success_at: now - 120, last_failure_at: null};
    let result: unknown = {};
    if (capability === 'collection.preferences') result = {sources: [{channel_id: 'one', title: 'Example'}], excluded_channels: []};
    if (capability === 'library.list') result = {assets: [], total: 0};
    if (capability === 'activity.list') result = {items: [], total: 0};
    if (capability === 'activity.snapshot') result = {
      queued: 2, acquiring: 1, generating: 0, ready: 3, failed: 0, unavailable: 0,
      drain_paused: false, model: 'gemini-3.8-flash', model_ready: true, batch: {completed: 3, limit: 100}, failures: [],
      runtime: {worker_alive: true, heartbeat_at: now},
      health: {capabilities: {subscriptions: available, captions: available}, notice,
        maintenance: {state: 'idle'}, progress: {last_stage_at: now - 10, last_ready_at: now - 120}},
      discovery: {status: 'partial', finished_at: now - 60, next_scan_at: now + 240,
        budget: {used_units: 25, local_daily_budget: 9000, minimum_daily_units: 3000, requests_by_endpoint: {'playlistItems.list': 25}}},
    };
    await route.fulfill({json: {ok: true, result}});
  });
  await page.goto('/');
  await page.getByTestId('nav-youtube').click();
  await expect(page.getByRole('button', {name: language === 'zh' ? '运行详情' : 'Runtime details', exact: true})).toBeVisible();
  return requests;
}

test('normal progress keeps diagnostics secondary; keyboard entry traps and restores focus', async ({page}) => {
  await page.setViewportSize({width: 1200, height: 900});
  const requests = await runtime(page);
  await expect(page.getByText('部分频道尚待检查', {exact: true})).toBeVisible();
  await expect(page.getByRole('region', {name: '能力状态'})).toHaveCount(0);
  await expect(page.getByText('YouTube Data API')).toHaveCount(0);
  await page.screenshot({path: 'test-results/runtime-main-zh.png', fullPage: true});
  const entry = page.getByRole('button', {name: '运行详情', exact: true});
  await entry.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', {name: '运行详情', exact: true});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('YouTube Data API')).toBeVisible();
  await expect(dialog.getByRole('region', {name: '能力状态'})).toBeVisible();
  await dialog.getByText('来源状态与预算', {exact: true}).click();
  await expect(dialog.getByText(/本机今日记录 25/)).toBeVisible();
  await expect(page.locator('.modal-scrim')).toHaveAttribute('data-modal-state', 'open');
  await page.screenshot({path: 'test-results/runtime-details-zh.png', fullPage: true});
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press('Tab');
    expect(await dialog.evaluate(node => node.contains(document.activeElement))).toBe(true);
  }
  expect(requests.every(name => ['activity.snapshot', 'activity.list', 'collection.preferences', 'library.list'].includes(name))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(entry).toBeFocused();
});

test('narrow English layout and reduced motion keep details readable without horizontal overflow', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.emulateMedia({reducedMotion: 'reduce', colorScheme: 'light'});
  await runtime(page, 'en');
  await page.getByRole('button', {name: 'Runtime details', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: 'Runtime details', exact: true});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Connection checks', {exact: true})).toBeVisible();
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.modal-scrim')).toHaveAttribute('data-modal-state', 'open');
  await page.screenshot({path: 'test-results/runtime-details-mobile-en.png', fullPage: true});
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('waiting explains the impact without inviting retries or opening login', async ({page}) => {
  const requests = await runtime(page, 'zh', {level: 'waiting', capability: 'captions', category: 'network', next_check_at: Date.now() / 1000 + 600});
  const wait = page.getByRole('status', {name: 'YouTube 等待说明'});
  await expect(wait).toContainText('字幕获取暂缓');
  await expect(wait).toContainText('已有文稿可以继续阅读');
  await expect(wait.getByRole('button', {name: '查看连接'})).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await wait.getByRole('button', {name: '查看运行详情'}).click();
  await expect(page.getByRole('dialog', {name: '运行详情'})).toBeVisible();
  expect(requests.some(name => name.startsWith('content_session.') || name === 'connection.status' || name === 'activity.retry')).toBe(false);
});


test('dark appearance retains quiet hierarchy and a fully opaque detail surface', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 1000});
  await runtime(page);
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await page.screenshot({path: 'test-results/runtime-main-dark.png', fullPage: true});
  await page.getByRole('button', {name: '运行详情', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: '运行详情', exact: true});
  await expect(page.locator('.modal-scrim')).toHaveAttribute('data-modal-state', 'open');
  await expect(dialog.getByText('YouTube Data API')).toBeVisible();
  await page.screenshot({path: 'test-results/runtime-details-dark.png', fullPage: true});
});
