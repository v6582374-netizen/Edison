import {cleanup, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, expect, it} from "vitest";
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
afterEach(cleanup);
const snapshot = () => ({model: "gemini-3.8-flash", model_ready: true,
  runtime: {worker_alive: true, heartbeat_at: Date.now() / 1000}, drain_paused: false,
}) as Activity;

it("does not imply a currently online worker from a stale snapshot", () => {
  render(<RuntimeDetails activity={snapshot()} receivedAt={Date.now() - 20000} disconnected={false} />);
  expect(screen.getByRole("status").textContent).toContain("最近收到的信息");
  expect(screen.queryByText("后台在线")).toBeNull();
  expect(screen.getByText("暂未确认")).toBeTruthy();
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
