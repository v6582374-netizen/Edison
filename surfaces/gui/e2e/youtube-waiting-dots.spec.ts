import {test, expect} from './fixtures';
import type {Page} from '@playwright/test';

async function progress(page: Page, language = 'en') {
  await page.addInitScript(lang => localStorage.setItem('openworker.lang', lang), language);
  let state: Record<string, unknown> = {};
  await page.route('**/v1/youtube/capability', async route => {
    const {capability} = route.request().postDataJSON();
    let result: unknown = {};
    if (capability === 'collection.preferences') result = {sources: [{channel_id: 'one', title: 'Example'}], excluded_channels: []};
    if (capability === 'library.list') result = {assets: [], total: 0};
    if (capability === 'activity.list') result = {items: [], total: 0};
    if (capability === 'activity.snapshot') result = {
      queued: 0, acquiring: 0, generating: 0, ready: 48, failed: 0, unavailable: 0,
      model_ready: true, drain_paused: false, batch: {completed: 0, limit: 100}, failures: [],
      runtime: {worker_alive: true, heartbeat_at: Date.now() / 1000, discovering: false}, ...state,
    };
    await route.fulfill({json: {ok: true, result}});
  });
  await page.goto('/');
  await page.getByTestId('nav-youtube').click();
  return (next: Record<string, unknown>) => {state = next;};
}

test('waiting replaces the orb with six decorative period positions at the content edge', async ({page}) => {
  await progress(page);
  const hero = page.getByRole('region', {name: 'Current progress'});
  await expect(hero.getByRole('heading', {name: 'Waiting for subscription updates', exact: true})).toBeVisible();
  await expect(hero.locator('.discovery-beacon-orb')).toHaveCount(0);
  const dots = hero.locator('.discovery-waiting-dots');
  await expect(dots).toHaveAttribute('aria-hidden', 'true');
  await expect(dots.locator('span')).toHaveCount(6);
  const title = hero.getByRole('heading');
  const content = page.locator('.yp-progress');
  expect(Math.abs((await title.boundingBox())!.x - (await content.boundingBox())!.x)).toBeLessThan(1);
});

test('periods advance one through six every 400ms without shifting text or metrics and honor live reduced motion', async ({page}) => {
  await page.setViewportSize({width: 1280, height: 850});
  await progress(page);
  const hero = page.getByRole('region', {name: 'Current progress'});
  const dots = hero.locator('.discovery-waiting-dots');
  await expect(dots.locator('span')).toHaveCount(6);
  const samples = [0, 399, 401, 799, 801, 1199, 1201, 1599, 1601, 1999, 2001, 2399, 2401];
  const counts: number[] = [];
  const geometries: unknown[] = [];
  for (const time of samples) {
    const result = await hero.evaluate((node, time) => {
      node.querySelectorAll('.discovery-waiting-dots > span').forEach(dot => dot.getAnimations().forEach(animation => {animation.pause(); animation.currentTime = time;}));
      const visible = [...node.querySelectorAll('.discovery-waiting-dots > span')].filter(dot => getComputedStyle(dot).opacity === '1').length;
      const geometry = ['.discovery-beacon-title', '.discovery-waiting-dots', '.discovery-beacon-metrics'].map(selector => {
        const {x, y, width, height} = node.querySelector(selector)!.getBoundingClientRect();
        return {x, y, width, height};
      });
      return {visible, geometry};
    }, time);
    counts.push(result.visible);
    geometries.push(result.geometry);
    if ([0, 401, 801, 1201, 1601, 2001].includes(time)) await hero.screenshot({path: `test-results/waiting-dots-stage-${result.visible}.png`});
  }
  expect(counts).toEqual([1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 1]);
  expect(geometries.every(value => JSON.stringify(value) === JSON.stringify(geometries[0]))).toBe(true);
  await expect(hero.getByRole('heading', {name: 'Waiting for subscription updates', exact: true})).toBeVisible();
  await page.waitForResponse(response => response.url().includes('/v1/youtube/capability') && response.request().postDataJSON().capability === 'activity.snapshot');
  expect(await dots.evaluate(node => node.getAnimations({subtree: true}).map(animation => animation.currentTime))).toEqual([2401, 2401, 2401, 2401, 2401]);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await expect.poll(() => dots.locator('span').evaluateAll(nodes => nodes.filter(node => getComputedStyle(node).opacity === '1').length)).toBe(3);
  expect(await dots.evaluate(node => node.getAnimations({subtree: true}).length)).toBe(0);
  await page.emulateMedia({reducedMotion: 'no-preference'});
  await expect.poll(() => dots.evaluate(node => node.getAnimations({subtree: true}).length)).toBe(5);
});

test('all non-processing waits use dots while actual discovery and preparation retain the orb', async ({page}) => {
  await page.clock.install();
  const update = await progress(page);
  const hero = page.getByRole('region', {name: 'Current progress'});
  const heading = hero.getByRole('heading');
  const states: [Record<string, unknown>, string, boolean][] = [
    [{queued: 2}, 'Waiting to process', true],
    [{awaiting_classification: 1}, 'Waiting for video classification', true],
    [{model_ready: false}, 'Model setup required', true],
    [{drain_paused: true}, 'Updates paused', true],
    [{youtube_requests: {cooldown_until: Date.now() / 1000 + 7200}}, 'YouTube cooldown', true],
    [{cutover: {held: true, state: 'draining'}}, 'YouTube cutover hold', true],
    [{runtime: {worker_alive: true, heartbeat_at: Date.now() / 1000, discovering: true}}, 'Checking subscriptions', false],
    [{current: [{video_id: 'sample', title: 'Sample video', channel_title: 'Example', preparation_state: 'generating', preparation_stage: 'composition', preparation_started_at: Date.now() / 1000}]}, 'Composition', false],
    [{}, 'Waiting for subscription updates', true],
  ];
  await expect(heading).toHaveText('Waiting for subscription updates......');
  for (const [state, label, waiting] of states) {
    update(state);
    await page.clock.fastForward(4500);
    await expect(heading).toHaveAccessibleName(label);
    await expect(hero.locator('.discovery-waiting-dots')).toHaveCount(waiting ? 1 : 0);
    await expect(hero.locator('.discovery-beacon-orb')).toHaveCount(waiting ? 0 : 1);
  }
  await page.route('**/v1/youtube/capability', async route => {
    if (route.request().postDataJSON().capability === 'activity.snapshot') await route.abort();
    else await route.fallback();
  });
  await page.clock.fastForward(4500);
  await expect(heading).toHaveAccessibleName('Reconnecting');
  await expect(hero.locator('.discovery-beacon-orb')).toHaveCount(0);
  await expect(hero.locator('.discovery-waiting-dots')).toHaveCount(1);
});

for (const language of ['zh', 'en']) {
  test(`narrow ${language} waiting layout preserves typography in light and dark appearances`, async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await progress(page, language);
    await page.keyboard.press('Meta+b');
    await expect(page.locator('.app')).toHaveClass(/nav-collapsed/);
    await expect.poll(() => page.locator('.sidebar').evaluate(node => node.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
    const hero = page.locator('.discovery-beacon-hero');
    await expect(hero.locator('.discovery-waiting-dots')).toBeVisible();
    await expect.poll(async () => (await hero.boundingBox())!.width).toBeGreaterThan(300);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await hero.locator('h2').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      const typography = await hero.locator('h2').evaluate(node => {
        const title = getComputedStyle(node);
        const dots = getComputedStyle(node.querySelector('.discovery-waiting-dots')!);
        return {title: [title.fontFamily, title.fontSize, title.color], dots: [dots.fontFamily, dots.fontSize, dots.color]};
      });
      expect(typography.dots).toEqual(typography.title);
      await page.screenshot({path: `test-results/waiting-dots-${language}-${theme}-mobile.png`, fullPage: true});
    }
  });
}
