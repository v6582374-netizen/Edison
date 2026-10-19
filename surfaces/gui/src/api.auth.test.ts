import { afterEach, expect, it, vi } from "vitest";
import { getHealth, Session, youtubeCapability } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("identifies an expired Edison login before reporting a YouTube failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: "login required" }), { status: 401 },
  )));
  await expect(youtubeCapability("session_cookies.status")).rejects.toThrow(
    "Edison 登录已失效，请重新登录。已许可的 YouTube 后台工作继续，无需重新连接 YouTube。",
  );
});

it("authenticates REST and session WebSocket calls with the launch token", async () => {
  vi.stubGlobal("__COWORKER_API_TOKEN__", "launch-token");
  const request = vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-OpenWorker-Token")).toBe("launch-token");
    return { json: async () => ({ status: "ok" }) } as Response;
  });
  vi.stubGlobal("fetch", request);

  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();

    constructor(
      public readonly url: string,
      public readonly protocols?: string | string[],
    ) {}
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);

  await getHealth();
  expect(request).toHaveBeenCalledOnce();

  const session = new Session("s1", "/workspace", "code", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  expect(socket.protocols).toEqual(["openworker", "launch-token"]);
});

it("returns an expired self-hosted workspace to login from any API", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { protocol: "https:", origin: "https://edison.example", assign });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: "login required" }), { status: 401 },
  )));
  await expect(getHealth()).rejects.toThrow("Edison 登录已失效");
  expect(assign).toHaveBeenCalledWith("/login");
});

it("does not redirect a provider or launch-token authorization failure to Edison login", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { protocol: "https:", origin: "https://edison.example", assign });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: "provider authorization required" }), { status: 401 },
  )));
  await expect(youtubeCapability("connection.status")).rejects.toThrow("401");
  expect(assign).not.toHaveBeenCalled();
});
