import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { harness } from './youtube-content-harness';

// Real Web -> real host loops -> temporary persistent library. Captions, Google
// and time are substitutes, so the product records every event as controlled:
// these flows prove notices, escalation, persistence and period timing, never
// natural coverage. Nothing here calls the scheduler or edits recovery state.
const alert = (page: Page) => page.getByRole('alert', {name: 'YouTube 能力提示'});
const waiting = (page: Page) => page.getByRole('status', {name: 'YouTube 等待说明'});
const localTime = (page: Page, at: number) => page.evaluate(value => new Date(value * 1000).toLocaleString(), at);
const SECRETS = ['fixture-access', 'fixture-session-', 'secret-refresh-e2e', 'secret-client-e2e'];

test('ordinary waits stay quiet, escalate after a day while probes continue, survive a crash and recover unattended', async ({page, request}) => {
  test.setTimeout(240000);
  const host = await harness(request);
  try {
    await host.connect(page);
    await host.home(page);
    const tools = page.locator('details.yp-maintenance-tools');
    await tools.locator('summary').click();
    const acceptance = page.getByRole('region', {name: '持续验收'});
    await expect(acceptance.getByRole('status')).toHaveText('持续验收：尚未开始');
    await acceptance.getByRole('button', {name: '开始持续验收'}).click();
    await expect(acceptance.getByRole('status')).toHaveText('持续验收：验收中');
    await expect(acceptance).toContainText('第 1 个连续周期');
    await expect(acceptance).toContainText('受控替身');
    await tools.locator('summary').click();

    await host.mode('network'); await host.advance(16); await host.submit('wait0000001');
    let attempts = 0;
    const probed = async () => {
      attempts += 1;
      await expect.poll(async () => {
        await host.advance(1);
        const state = (await host.asset('wait0000001')).preparation_state;
        return (await host.captions('wait0000001')).length >= attempts && !['queued', 'acquiring'].includes(state);
      }, {timeout: 30000}).toBe(true);
    };
    await probed();
    await expect(waiting(page)).toContainText('字幕能力 · 网络暂时中断');
    await expect(waiting(page)).toContainText('下一次检查');
    await expect(alert(page)).toHaveCount(0);
    const health = page.getByRole('region', {name: '能力状态'});
    await expect(health).toContainText('等待自动恢复');
    await expect(health).toContainText('后台心跳只说明服务在线');
    const since = (await host.call('activity.snapshot')).health.notice.since;

    // 10/20/40 minutes, then six-hourly shared probes: still an ordinary wait.
    for (const wait of [600, 1200, 2400, 21600, 21600, 21600]) {
      await host.advance(wait); await probed();
      await expect(waiting(page)).toBeVisible();
      await expect(alert(page)).toHaveCount(0);
    }
    await host.advance(21600); await probed();
    await expect(alert(page)).toContainText('字幕能力已中断超过 24 小时');
    await expect(alert(page)).toContainText('无需点击重试');
    await expect(alert(page)).toContainText(`开始于：${await localTime(page, since)}`);
    await expect(waiting(page)).toHaveCount(0);
    // Escalation is a notice, not a stop: the shared probe keeps its schedule.
    await host.advance(21600); await probed();

    await host.stop('SIGKILL'); await host.start();
    await host.home(page);
    await expect(alert(page)).toContainText('字幕能力已中断超过 24 小时');
    await expect(alert(page)).toContainText(`开始于：${await localTime(page, since)}`);
    expect((await host.call('acceptance.status')).current).toMatchObject({number: 1, state: 'observing'});

    await host.mode('good'); await host.advance(21600);
    await host.manuscript('wait0000001');
    await expect(alert(page)).toHaveCount(0);
    await expect(waiting(page)).toHaveCount(0);
    await expect(health).toContainText('可用');

    const evidence = await host.call('acceptance.export');
    const outcomes = evidence.events.filter((event: any) => event.kind === 'fault' && event.capability === 'captions').map((event: any) => event.detail.outcome);
    expect(outcomes).toEqual(['started', 'recovered']);
    expect(evidence.events.filter((event: any) => event.kind === 'notice').map((event: any) => event.detail.level))
      .toEqual(expect.arrayContaining(['waiting', 'escalated', 'none']));
    expect(evidence.events.filter((event: any) => event.kind === 'host').map((event: any) => event.detail.mode)).toEqual(['first', 'crash']);
    expect(evidence.events.some((event: any) => event.kind === 'task' && event.detail.stage === 'ready' && event.detail.video_id === 'wait0000001')).toBe(true);
    expect(evidence.events.every((event: any) => event.controlled)).toBe(true);
    const current = evidence.status.current;
    expect(current).toMatchObject({number: 1, state: 'observing', interventions: {ordinary: 0}});
    // Controlled recovery and restarts never stand in for natural coverage.
    const covered = Object.fromEntries(current.coverage.map((item: any) => [item.key, item.covered]));
    expect(covered).toMatchObject({fault_recovery: false, host_restart: false, service_restart: false});
    for (const secret of SECRETS) expect(JSON.stringify(evidence)).not.toContain(secret);
  } finally { await host.cleanup(page); }
});

test('verification, maintenance and reauthorization alert at once; reconnection opens a new period and fourteen days never pass alone', async ({page, request}) => {
  test.setTimeout(360000);
  const host = await harness(request);
  try {
    await host.connect(page);
    await host.call('acceptance.begin');
    await host.home(page);

    // A maintained snapshot is refused again: Google needs the person now.
    await host.refuse('fixture-session-1', 'fixture-session-2');
    await host.advance(16); await host.submit('hand0000001');
    await expect.poll(async () => { await host.advance(1); return host.starts(); }, {timeout: 30000}).toBe(2);
    await host.advance(50);
    await expect.poll(async () => { await host.advance(1); return (await host.session()).maintenance.state; }, {timeout: 30000}).toBe('handoff');
    await expect(alert(page)).toContainText('需要你完成 Google 登录或安全验证');
    await expect(alert(page)).toContainText('后台已停止相关自动尝试');
    await expect.poll(async () => (await host.call('acceptance.status')).current.security_verifications)
      .toEqual({natural: 0, limit: 3, controlled: 1});
    const content = await host.panel(page);
    await content.getByRole('button', {name: '打开登录画面', exact: true}).click();
    await content.getByAltText('服务器登录页面').click();
    await content.getByRole('checkbox').check();
    await content.getByLabel('用于检查字幕的视频链接').fill('https://youtu.be/abcdefghijk');
    await host.advance(30);  // the check still honours the 15-second video pacing
    await content.getByRole('button', {name: '完成登录并检查字幕'}).click();
    await expect(content.getByRole('status')).toHaveText('字幕能力已验证');
    await expect(alert(page)).toHaveCount(0);
    await host.manuscript('hand0000001');
    // Completing a verification is not a reconnection: the period continues.
    expect((await host.call('acceptance.status')).current.number).toBe(1);

    // Storage, permission or program faults are maintenance, shown immediately.
    await host.home(page);
    await host.mode('maintenance'); await host.advance(16); await host.submit('fail0000001');
    await expect.poll(async () => { await host.advance(1); return (await host.session()).failure_kind; }, {timeout: 30000}).toBe('maintenance');
    await expect(alert(page)).toContainText('需要维护');
    await expect(alert(page)).toContainText('不是重新登录能解决');
    await host.mode('good'); await host.advance(16); await host.submit('good0000001');
    await host.manuscript('good0000001');
    await expect(alert(page)).toHaveCount(0);

    // An unexplained invalid_grant needs reauthorization; it is not called a security challenge.
    await host.call('activity.resume');
    await host.flag('oauth-invalid-grant');
    await expect.poll(async () => { await host.advance(3600); return (await host.call('activity.snapshot')).health.notice.category; }, {timeout: 60000}).toBe('reauthorization');
    await expect(alert(page)).toContainText('需要重新授权 Google');
    await expect(alert(page)).toContainText('未经证实为安全验证');
    await host.flag('oauth-invalid-grant', false);
    await host.call('connection.import_authorization', {client_id: 'secret-client-e2e', client_secret: 'secret-client-e2e', refresh_token: 'secret-refresh-e2e'});
    const reconnected = await host.call('acceptance.status');
    expect(reconnected.current).toMatchObject({number: 2, reason: 'Google 官方授权重新连接', state: 'observing'});
    expect(reconnected.history).toHaveLength(1);
    expect(reconnected.history[0]).toMatchObject({number: 1, state: 'unverified'});

    await host.advance(14 * 86400 + 1);
    const tools = page.locator('details.yp-maintenance-tools');
    await tools.locator('summary').click();
    const acceptance = page.getByRole('region', {name: '持续验收'});
    await expect(acceptance.getByRole('status')).toHaveText('持续验收：尚未验证');
    await expect(acceptance).toContainText('第 2 个连续周期');
    await expect(acceptance).toContainText('计时：已满 14 / 14 天');
    await expect(acceptance.getByRole('list', {name: '关键事件覆盖'})).toContainText('缺 自然 Cookie 轮换后的会话更新');
    await expect(acceptance).toContainText('此前周期 1 个');
    const [download] = await Promise.all([page.waitForEvent('download'), acceptance.getByRole('button', {name: '导出验收证据'}).click()]);
    const exported = await readFile(await download.path(), 'utf8');
    const evidence = JSON.parse(exported);
    expect(evidence.periods.map((period: any) => period.number)).toEqual([1, 2]);
    const faults = evidence.events.filter((event: any) => event.kind === 'fault' && ['started', 'changed'].includes(event.detail.outcome))
      .map((event: any) => `${event.capability}:${event.detail.category}`);
    expect(faults).toEqual(['captions:session', 'captions:user', 'captions:maintenance', 'subscriptions:reauthorization']);
    for (const secret of SECRETS) expect(exported).not.toContain(secret);

    // Health reads stay readable under a drained hold without adding an
    // untracked writer while backup takes its consistent copy.
    await host.call('cutover.begin');
    await expect.poll(async () => (await host.call('cutover.status')).drained).toBe(true);
    const before = await host.call('acceptance.export');
    for (let index = 0; index < 3; index++) await host.call('activity.snapshot');
    expect(await host.call('acceptance.export')).toEqual(before);
    expect((await host.raw('acceptance.drill', {active: true})).kind).toBe('cutover_hold');
  } finally { await host.cleanup(page); }
});
