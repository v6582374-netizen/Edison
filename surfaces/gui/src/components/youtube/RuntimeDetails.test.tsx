import {act, cleanup, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import i18n from "i18next";
import zh from "../../locales/zh.json";
import en from "../../locales/en.json";
import {RuntimeDetails} from "./RuntimeDetails";
import type {Activity} from "./types";

beforeEach(() => {
  i18n.addResourceBundle("zh", "translation", zh);
  i18n.addResourceBundle("en", "translation", en);
  void i18n.changeLanguage("zh");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const snapshot = () => ({model: "gemini-3.8-flash", model_ready: true,
  runtime: {worker_alive: true, heartbeat_at: Date.now() / 1000}, drain_paused: false,
}) as Activity;

it("does not imply a currently online worker from a stale snapshot", () => {
  render(<RuntimeDetails activity={snapshot()} receivedAt={Date.now() - 20000} disconnected={false} />);
  expect(screen.getByRole("status").textContent).toContain("最近收到的信息");
  expect(screen.queryByText("后台在线")).toBeNull();
  expect(screen.getByText("暂未确认")).toBeTruthy();
});
it("expires online and snapshot freshness while open without contacting the server", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-25T08:00:00Z"));
  const fetch = vi.spyOn(globalThis, "fetch");
  const view = render(<RuntimeDetails activity={snapshot()} receivedAt={Date.now()} disconnected={false} />);
  expect(screen.getByText("后台在线")).toBeTruthy();
  act(() => { vi.advanceTimersByTime(9000); });
  expect(screen.getByText("后台在线")).toBeTruthy();
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.queryByText("后台在线")).toBeNull();
  expect(screen.getByText("后台心跳未确认")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  act(() => { vi.advanceTimersByTime(3000); });
  expect(screen.getByRole("status").textContent).toContain("最近收到的信息");
  expect(screen.getByText("暂未确认")).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
it("keeps a disconnected snapshot unconfirmed and missing coverage explicit", () => {
  render(<RuntimeDetails activity={snapshot()} receivedAt={Date.now()} disconnected />);
  expect(screen.queryByText("后台在线")).toBeNull();
  expect(screen.getByText("连接检查尚无记录。")).toBeTruthy();
});
it("localizes the initial waiting state", async () => {
  await i18n.changeLanguage("en");
  render(<RuntimeDetails activity={null} receivedAt={0} disconnected={false} />);
  expect(screen.getByRole("status").textContent).toBe("Waiting for runtime information from the server.");
});
