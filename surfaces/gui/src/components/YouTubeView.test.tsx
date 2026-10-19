import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { YouTubeView } from "./YouTubeView";
import i18n from "i18next";
import zh from "../locales/zh.json";
import { youtubeCapability } from "../api";
vi.mock("../api", () => ({ youtubeCapability: vi.fn() }));
const call = vi.mocked(youtubeCapability);
const asset = {
  video_id: "one",
  channel_id: "channel",
  channel_title: "Example",
  title: "Prepared manuscript",
  url: "https://www.youtube.com/watch?v=one",
  published_at: new Date().toISOString(),
  preparation_state: "ready",
  reading_state: "inbox",
  manuscript_version: 1,
  excerpt: "Useful source text",
  manuscript_characters: 1500,
};
const activity = {
  queued: 1,
  acquiring: 0,
  generating: 0,
  ready: 1,
  failed: 0,
  unavailable: 0,
  drain_paused: false,
  batch: { completed: 1, limit: 100 },
  model: "host/model",
  model_ready: false,
  failures: [],
};
beforeEach(() => {
  i18n.addResourceBundle("zh", "translation", zh);
  void i18n.changeLanguage("zh");
  call.mockReset();
  call.mockImplementation(async (capability) => {
    if (capability === "library.list") return { assets: [asset] };
    if (capability === "activity.snapshot") return activity;
    if (capability === "activity.list") return {items: [], total: 0};
    if (capability === "collection.preferences")
      return {
        sources: [{ channel_id: "channel", title: "Example" }],
        excluded_channels: [],
      };
    if (capability === "generation.prompt")
      return {
        prompt: "Original instruction",
        default_prompt: "Default instruction",
      };
    if (capability === "collection.set_exclusions")
      return {
        sources: [{ channel_id: "channel", title: "Example" }],
        excluded_channels: ["channel"],
      };
    if (capability === "library.inspect")
      return {
        ...asset,
        source_trace: { video_url: asset.url, transcript_available: true },
        generation_records: [],
      };
    if (capability === "connection.status") return new Promise(() => {});
    return {};
  });
});
afterEach(cleanup);
function renderDocuments(onTranslationSettings: () => void) {
  const result = render(<YouTubeView onModelSettings={() => {}} onTranslationSettings={onTranslationSettings} />);
  fireEvent.click(screen.getByRole("button", {name: "阅读文档"}));
  return result;
}

describe("YouTube document views", () => {
  it("retries an unsaved summary when the document is opened again", async () => {
    const implementation = call.getMockImplementation()!;
    let attempts = 0;
    call.mockImplementation(async (capability, args) => {
      if (capability === "summary.ensure") {
        attempts++;
        if (attempts === 1) throw new Error("模型暂不可用");
        return { summary: "整篇文稿的独立概览。" };
      }
      return implementation(capability, args);
    });
    renderDocuments(() => {});
    fireEvent.click(await screen.findByRole("button", { name: /Prepared manuscript/ }));
    await screen.findByText("模型暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "关闭文档信息" }));
    fireEvent.click(screen.getByRole("button", { name: /Prepared manuscript/ }));
    await screen.findByText("整篇文稿的独立概览。");
    expect(attempts).toBe(2);
  });
  it("shows the saved channel image across views and falls back when it fails", async () => {
    const implementation = call.getMockImplementation()!;
    const thumbnailUrl = "https://yt3.googleusercontent.com/channel-image";
    call.mockImplementation(async (capability, args) =>
      capability === "collection.preferences"
        ? { sources: [{ channel_id: "channel", title: "Example", thumbnail_url: thumbnailUrl }], excluded_channels: [] }
        : implementation(capability, args),
    );
    const { container } = renderDocuments(() => {});
    await screen.findByRole("button", { name: /Prepared manuscript/ });

    fireEvent.click(screen.getByRole("button", { name: "文档库" }));
    const libraryAvatar = container.querySelector(".yp-document-top .yp-avatar")!;
    await waitFor(() => expect(libraryAvatar.querySelector("img")?.getAttribute("src")).toBe(thumbnailUrl));
    fireEvent.error(libraryAvatar.querySelector("img")!);
    expect(libraryAvatar.textContent).toBe("E");
    expect(libraryAvatar.querySelector("img")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "频道索引" }));
    expect(container.querySelector(".yp-source-nav .yp-avatar img")?.getAttribute("src")).toBe(thumbnailUrl);

    fireEvent.click(screen.getByRole("button", { name: /Prepared manuscript/ }));
    await screen.findByText("Useful source text");
    expect(container.querySelector(".yp-inspector-author .yp-avatar img")?.getAttribute("src")).toBe(thumbnailUrl);

    fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
    await screen.findByRole("checkbox", { name: "自动生成 Example" });
    expect(document.querySelector(".yp-channel-row .yp-avatar img")?.getAttribute("src")).toBe(thumbnailUrl);
  });
  it("keeps host configuration out of the normal UI and opens connection immediately", async () => {
    renderDocuments(() => {});
    await screen.findByRole("button", { name: /Prepared manuscript/ });
    expect(screen.queryByText(/host\/model/)).toBeNull();
    expect(screen.queryByRole("button", { name: "收件箱" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "连接 YouTube" }),
    );
    expect(screen.getByRole("dialog", { name: "连接 YouTube" })).toBeTruthy();
  });
  it("offers three presentations without clearing the shared filters and remembers the choice", async () => {
    const component = renderDocuments(() => {});
    await screen.findByRole("button", { name: /Prepared manuscript/ });
    fireEvent.change(screen.getByRole("combobox", { name: "按时间筛选" }), {
      target: { value: "all" },
    });
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索文档" }), {
      target: { value: "science" },
    });
    for (const name of ["文档库", "频道索引", "时间流"]) {
      fireEvent.click(screen.getByRole("button", { name }));
      expect(
        (
          screen.getByRole("searchbox", {
            name: "搜索文档",
          }) as HTMLInputElement
        ).value,
      ).toBe("science");
      expect(
        (
          screen.getByRole("combobox", {
            name: "按时间筛选",
          }) as HTMLSelectElement
        ).value,
      ).toBe("all");
    }
    fireEvent.click(screen.getByRole("button", { name: "文档库" }));
    component.unmount();
    renderDocuments(() => {});
    expect(
      screen
        .getByRole("button", { name: "文档库" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });
  it("saves exclusions and opens the shared translation settings", async () => {
    const openTranslation = vi.fn();
    renderDocuments(openTranslation);
    await screen.findByRole("button", { name: /Prepared manuscript/ });
    fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "自动生成 Example" }),
    );
    expect(
      call.mock.calls.some(([c]) => c === "collection.set_exclusions"),
    ).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(call).toHaveBeenCalledWith("collection.set_exclusions", {
        excluded_channels: ["channel"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "生成规则" }));
    expect(openTranslation).toHaveBeenCalledOnce();
  });
  it("shows provenance without automatically opening a document", async () => {
    renderDocuments(() => {});
    fireEvent.click(
      await screen.findByRole("button", { name: /Prepared manuscript/ }),
    );
    const dialog = await screen.findByLabelText("文档信息");
    const open = await within(dialog).findByRole("button", {
      name: "用默认应用打开",
    });
    expect(call.mock.calls.some(([c]) => c === "documents.open")).toBe(false);
    fireEvent.click(open);
    await waitFor(() =>
      expect(call).toHaveBeenCalledWith("documents.open", { video_id: "one" }),
    );
  });
});

it("never exposes the previous document after the next inspection fails", async () => {
  const implementation = call.getMockImplementation()!;
  call.mockImplementation(async (capability, args) => {
    if (capability === "library.list")
      return {
        assets: [
          asset,
          { ...asset, video_id: "two", title: "Unavailable document" },
        ],
      };
    if (capability === "library.inspect" && args?.video_id === "two")
      throw new Error("文档不可用");
    return implementation(capability, args);
  });
  renderDocuments(() => {});
  fireEvent.click(
    await screen.findByRole("button", { name: /Prepared manuscript/ }),
  );
  await screen.findByRole("button", { name: "删除文档" });
  const dialog = screen.queryByRole("dialog", {name: "文档信息"});
  if (dialog) fireEvent.click(within(dialog).getByRole("button", {name: "关闭"}));
  fireEvent.click(screen.getByRole("button", { name: /Unavailable document/ }));
  await screen.findByText("文档不可用");
  expect(
    within(screen.getByLabelText("文档信息")).queryByRole("button", {
      name: "删除文档",
    }),
  ).toBeNull();
});

it("shows the verified connection on the channel button, and removes it after unified disconnect", async () => {
  const implementation = call.getMockImplementation()!;
  let connected = true;
  call.mockImplementation(async (capability, args) => {
    if (capability === "connection.disconnect") connected = false;
    if (capability.startsWith("connection."))
      return { configured: true, authorized: connected, subscription_count: 3, managed_content: true,
        disconnection: connected ? {} : { local: "cleared", google: "revoked" } };
    return implementation(capability, args);
  });
  renderDocuments(() => {});
  fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "已连接 YouTube" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "断开 YouTube" }));
  await screen.findByText("本实例已清除 Google 官方授权与 YouTube 内容会话。");
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
  expect(
    await screen.findByRole("button", { name: "连接 YouTube" }),
  ).toBeTruthy();
});

it("loads an existing authorization on a fresh view without inferring it from imported channels", async () => {
  const implementation = call.getMockImplementation()!;
  call.mockImplementation(async (capability, args) =>
    (capability === "connection.status" || capability === "connection.refresh_subscriptions")
      ? { configured: true, authorized: true, subscription_count: 12 }
      : implementation(capability, args),
  );
  renderDocuments(() => {});
  fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
  expect(
    await screen.findByRole("button", { name: "已连接 YouTube" }),
  ).toBeTruthy();
  expect(await screen.findByText("12 个订阅")).toBeTruthy();
});

it("reports unknown connection status on failure instead of showing a false connected badge", async () => {
  const implementation = call.getMockImplementation()!;
  call.mockImplementation(async (capability, args) => {
    if (capability === "connection.status")
      throw new Error("Vault unavailable");
    return implementation(capability, args);
  });
  renderDocuments(() => {});
  fireEvent.click(screen.getByRole("button", { name: "订阅频道" }));
  await screen.findByText("连接状态未知，请重试");
  expect(screen.queryByRole("button", { name: "已连接 YouTube" })).toBeNull();
});

it("hands the original video to the native browser capability rather than a webview popup", async () => {
  renderDocuments(() => {});
  fireEvent.click(
    await screen.findByRole("button", { name: /Prepared manuscript/ }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "打开 YouTube" }));
  await waitFor(() =>
    expect(call).toHaveBeenCalledWith("sources.open", { video_id: "one" }),
  );
  await screen.findByText("已在默认浏览器中打开原始视频。");
});
