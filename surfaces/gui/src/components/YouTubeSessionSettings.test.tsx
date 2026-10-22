import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { YouTubeSessionSettings, type ContentSessionStatus } from "./YouTubeSessionSettings";
import { youtubeCapability } from "../api";
vi.mock("../api", () => ({ youtubeCapability: vi.fn(), YouTubeCapabilityError: class extends Error {} }));
const call = vi.mocked(youtubeCapability);
const base: ContentSessionStatus = {
  state: "maintaining", browser_running: false, snapshot_present: true,
  reason: "内容会话需要维护，后台将自动打开服务器浏览器（每次最多 3 分钟），完成后继续获取字幕。",
  maintenance: { state: "idle", basis: null, reason: null, next_at: null, deadline_at: null, used: 0, limit: 2 },
};
const at = (seconds: number) => new Date(seconds * 1000).toLocaleString();
beforeEach(() => { call.mockReset(); });
afterEach(cleanup);

it("shows why unattended maintenance waits and when it can next act", async () => {
  call.mockResolvedValue({ ...base, maintenance: { ...base.maintenance, state: "waiting_budget", basis: "signed_out", used: 2,
    reason: "自动维护在滚动 24 小时内已用完 2 次，等待下一窗口；需要时可亲自打开服务器浏览器。", next_at: 1_800_000_000 } });
  render(<YouTubeSessionSettings />);
  expect(await screen.findByText("后台正在维护内容会话")).toBeTruthy();
  const note = screen.getByRole("note", { name: "自动会话维护" });
  expect(note.textContent).toContain("已用完 2 次");
  expect(note.textContent).toContain(`下一次可执行：${at(1_800_000_000)}`);
  expect(note.textContent).toContain("滚动 24 小时已用 2/2 次");
});

it("never exposes the unattended maintenance browser as an input view", async () => {
  call.mockImplementation(async capability => capability === "content_session.status"
    ? { ...base, browser_running: true, maintenance: { ...base.maintenance, state: "running", used: 1, deadline_at: 1_800_000_180,
      reason: "后台正在自动维护内容会话，最多 3 分钟后关闭浏览器。" } }
    : { image: "", width: 1, height: 1 });
  render(<YouTubeSessionSettings />);
  const note = await screen.findByRole("note", { name: "自动会话维护" });
  expect(note.textContent).toContain(`最迟 ${at(1_800_000_180)} 关闭`);
  expect(screen.queryByText(/闲置 15 分钟自动关闭/)).toBeNull();
  expect(call.mock.calls.some(([capability]) => capability === "content_session.screen")).toBe(false);
  expect(screen.getByRole("button", { name: "打开登录画面" })).toBeTruthy();
});
