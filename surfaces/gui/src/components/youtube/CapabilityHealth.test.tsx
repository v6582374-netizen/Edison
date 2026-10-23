import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "i18next";
import zh from "../../locales/zh.json";
import { CapabilityHealth, CapabilityNotice } from "./CapabilityHealth";
import { AcceptancePanel } from "./AcceptancePanel";
import type { Acceptance, Health } from "./types";
vi.mock("../../api", () => ({ youtubeCapability: vi.fn() }));
beforeEach(() => {
  i18n.addResourceBundle("zh", "translation", zh);
  void i18n.changeLanguage("zh");
});
afterEach(cleanup);

const idle = { state: "available", fault: null, reason: null, next_check_at: null, last_success_at: 1_800_000_000, last_failure_at: null, since: null };
const health = (notice: Health["notice"]): Health => ({
  capabilities: { subscriptions: idle, captions: { ...idle, state: "waiting", fault: "network", next_check_at: 1_800_000_600 } },
  notice,
  maintenance: { state: "idle", reason: null, last_started_at: null, last_ended_at: null, last_basis: null, last_result: null },
  progress: { last_stage_at: null, last_ready_at: null },
});
const at = (seconds: number) => new Date(seconds * 1000).toLocaleString();

it("keeps an ordinary wait as a quiet status and escalates without asking for a retry", () => {
  const { rerender } = render(<CapabilityNotice health={health({ level: "waiting", capability: "captions", category: "network",
    reason: "字幕服务网络暂时不可用，稍后检查。", since: 1_800_000_000, next_check_at: 1_800_000_600 })} />);
  expect(screen.getByRole("status", { name: "YouTube 等待说明" }).textContent).toContain(`下一次检查：${at(1_800_000_600)}`);
  expect(screen.queryByRole("alert")).toBeNull();
  rerender(<CapabilityNotice health={health({ level: "escalated", capability: "captions", category: "network",
    reason: "x", since: 1_800_000_000, next_check_at: 1_800_021_600 })} />);
  const alert = screen.getByRole("alert", { name: "YouTube 能力提示" });
  expect(alert.textContent).toContain("字幕能力已中断超过 24 小时");
  expect(alert.textContent).toContain("无需点击重试");
  expect(alert.textContent).toContain(`开始于：${at(1_800_000_000)}`);
});

it("separates security verification, reauthorization and maintenance guidance", () => {
  const { rerender } = render(<CapabilityNotice health={health({ level: "attention", capability: "captions", category: "user" })} />);
  expect(screen.getByRole("alert").textContent).toContain("后台已停止相关自动尝试");
  rerender(<CapabilityNotice health={health({ level: "attention", capability: "subscriptions", category: "reauthorization" })} />);
  expect(screen.getByRole("alert").textContent).toContain("未经证实为安全验证");
  rerender(<CapabilityNotice health={health({ level: "attention", capability: "captions", category: "storage" })} />);
  expect(screen.getByRole("alert").textContent).toContain("不是重新登录能解决");
  rerender(<CapabilityNotice health={health({ level: "none" })} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("shows capability evidence apart from the heartbeat", () => {
  render(<CapabilityHealth health={health({ level: "none" })} />);
  const region = screen.getByRole("region", { name: "能力状态" });
  expect(region.textContent).toContain("等待自动恢复 · 网络暂时中断");
  expect(region.textContent).toContain("后台心跳只说明服务在线");
});

it("reports missing coverage and never shows a calendar pass", () => {
  const acceptance: Acceptance = { state: "unverified", drill: false, controlled_boundary: false, history: [],
    current: { state: "unverified", number: 2, started_at: 1_800_000_000, ended_at: null, reason: "Google 官方授权重新连接",
      elapsed_seconds: 14 * 86400, required_days: 14, complete_days: 14, missing_days: [3],
      coverage: [{ key: "natural_rotation", label: "自然 Cookie 轮换后的会话更新", covered: false },
        { key: "token_refresh", label: "正常令牌刷新", covered: true }],
      missing: ["自然 Cookie 轮换后的会话更新"], failures: [],
      security_verifications: { natural: 1, limit: 3, controlled: 2 },
      interventions: { total: 1, ordinary: 0, by_reason: { security_verification: 1 }, controlled: 0 },
      controlled_events: 4, natural_events: 20 } };
  render(<AcceptancePanel acceptance={acceptance} onChange={async () => {}} onError={() => {}} />);
  const region = screen.getByRole("region", { name: "持续验收" });
  expect(screen.getByRole("status").textContent).toBe("持续验收：尚未验证");
  expect(region.textContent).toContain("缺少每日记录：第 3 天");
  expect(region.textContent).toContain("非主动 Google 安全验证：1 / 3 次 · 受控演练 2 次另计");
  expect(screen.getByRole("list", { name: "关键事件覆盖" }).textContent).toContain("缺 自然 Cookie 轮换后的会话更新");
  expect(screen.getByRole("button", { name: "开始故障演练" })).toBeTruthy();
});
