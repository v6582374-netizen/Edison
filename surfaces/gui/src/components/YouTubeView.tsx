import { useTranslation } from "react-i18next";
import { yt } from "./youtube/text";
import { useCallback, useEffect, useRef, useState } from "react";
import { youtubeCapability as call } from "../api";
import { Icon } from "./Icon";
import { NoticeStack } from "./NoticeStack";
import { ActivityPage } from "./youtube/ActivityPage";
import { ProgressPage } from "./youtube/ProgressPage";
import { useModalInteraction, type ModalInput } from "./ModalSurface";
import { AddVideo, type VideoRequest } from "./youtube/AddVideo";
import { RequestedVideo, RequestedDocument } from "./youtube/RequestedVideo";
import { YouTubeSessionSettings } from "./YouTubeSessionSettings";
import { OfficialAuthorization } from "./youtube/OfficialAuthorization";
import { CapabilityNotice } from "./youtube/CapabilityHealth";
import { AcceptancePanel } from "./youtube/AcceptancePanel";
import { Dialog } from "./youtube/Dialog";
import { DocumentViews, ChannelAvatar } from "./youtube/DocumentViews";
import {
  Activity,
  Asset,
  Connection,
  Inspection,
  Layout,
  Preferences,
  Source,
  publicationLabel,
  videoDurationLabel,
  readingTime,
} from "./youtube/types";
import "./youtube/youtube.css";

const layoutKey = "edison:youtube:layout";
const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : String(e);
function savedLayout(): Layout {
  try {
    const value = localStorage.getItem(layoutKey);
    return ["timeline", "library", "channels"].includes(value || "") ? (value as Layout) : "timeline";
  } catch {
    return "timeline";
  }
}

export function YouTubeView({
  onModelSettings,
  onTranslationSettings,
}: {
  onModelSettings: () => void;
  onTranslationSettings: () => void;
}) {
  useTranslation();
  const modal = useModalInteraction();
  const mainRef = useRef<HTMLElement>(null);
  const layouts: [Layout, string][] = [
    ["timeline", yt("时间流")], ["library", yt("文档库")], ["channels", yt("频道索引")],
  ];
  const [layout, setLayout] = useState<Layout>(savedLayout);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [prefs, setPrefs] = useState<Preferences>({
    sources: [],
    excluded_channels: [],
  });
  const [progressActions, setProgressActions] = useState<HTMLDivElement | null>(null);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState("7");
  const [channel, setChannel] = useState("");
  const [unread, setUnread] = useState(false);
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [message, setMessage] = useState("");
  const [progressMessage, setProgressMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [panel, setPanel] = useState<
    "channels" | "connection" | "document" | null
  >(null);
  const [panelMessage, setPanelMessage] = useState("");
  const [panelLoading, setPanelLoading] = useState(false);
  const [panelReady, setPanelReady] = useState(false);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [channelSearch, setChannelSearch] = useState("");
  const [connection, setConnection] = useState<Connection | null>(null);
  const [subscriptionsSyncing, setSubscriptionsSyncing] = useState(false);
  const [connectionChecking, setConnectionChecking] = useState(false);
  const [connectionError, setConnectionError] = useState(false);
  const connectionEpoch = useRef(0);
  const connectionRequest = useRef<Promise<Connection> | null>(null);
  const checkConnection = async () => {
    const epoch = connectionEpoch.current;
    setConnectionChecking(true);
    setConnectionError(false);
    if (!connectionRequest.current) {
      const request = call<Connection>("connection.status");
      connectionRequest.current = request;
      void request
        .finally(() => {
          if (connectionRequest.current === request)
            connectionRequest.current = null;
        })
        .catch(() => {});
    }
    try {
      const result = await connectionRequest.current;
      if (epoch === connectionEpoch.current) setConnection(result);
      return result;
    } catch (error) {
      if (epoch === connectionEpoch.current) {
        setConnection(null);
        setConnectionError(true);
      }
      throw error;
    } finally {
      if (epoch === connectionEpoch.current) setConnectionChecking(false);
    }
  };
  const [contentRevision, setContentRevision] = useState(0);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [selected, setSelected] = useState<Inspection | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [page, setPage] = useState<"documents" | "progress" | "activity" | "add">("progress");
  const [activityReceivedAt, setActivityReceivedAt] = useState(0);
  const [activityError, setActivityError] = useState(false);
  const revision = useRef(0),
    panelRevision = useRef(0);
  const preferences = useCallback(async () => {
    const value = await call<Preferences>("collection.preferences");
    setPrefs(value);
    return value;
  }, []);
  const refresh = useCallback(async () => {
    const version = ++revision.current;
    const filters: Record<string, unknown> = {
      query,
      documents_only: true,
      unread_only: unread,
    };
    if (channel) filters.channel_id = channel;
    if (period === "custom") {
      if (after)
        filters.published_after =
          new Date(`${after}T00:00:00`).getTime() / 1000;
      if (before)
        filters.published_before =
          new Date(`${before}T23:59:59`).getTime() / 1000;
      if (after && before && after > before) {
        setMessage(yt("开始日期不能晚于结束日期。"));
        setLoading(false);
        return;
      }
    } else if (period !== "all") {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      start.setDate(start.getDate() - Number(period) + 1);
      filters.published_after = start.getTime() / 1000;
    }
    const list = await call<{ assets: Asset[] }>("library.list", filters);
    if (version !== revision.current) return;
    setAssets(list.assets);
    setSelected((current) => {
      if (!current) return current;
      const latest = list.assets.find(
        (asset) => asset.video_id === current.video_id,
      );
      return latest ? { ...current, ...latest } : current;
    });
    setLoading(false);
  }, [query, period, channel, unread, after, before]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const update = async () => {
      try {
        await refresh();
      } catch (e) {
        if (alive) {
          setMessage(errorMessage(e));
          setLoading(false);
        }
      }
      if (alive) timer = setTimeout(update, 4000);
    };
    void update();
    return () => {
      alive = false;
      revision.current++;
      clearTimeout(timer);
    };
  }, [refresh]);
  useEffect(() => {
    void preferences().catch((e) => setMessage(errorMessage(e)));
  }, [preferences]);
  const activityRevision = useRef(0);
  const refreshActivity = useCallback(async () => {
    const version = ++activityRevision.current;
    try {
      const value = await call<Activity>("activity.snapshot");
      if (version !== activityRevision.current) return;
      setActivity(value);
      setActivityReceivedAt(Date.now());
      setActivityError(false);
    } catch (e) {
      if (version === activityRevision.current) setActivityError(true);
      throw e;
    }
  }, []);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const update = async () => {
      try { await refreshActivity(); } catch { /* Keep last observation, mark stale. */ }
      if (alive) timer = setTimeout(update, 4000);
    };
    void update();
    return () => { alive = false; activityRevision.current++; clearTimeout(timer); };
  }, [refreshActivity]);
  const close = (input: ModalInput = modal.input()) => {
    if (panel !== "document") modal.end(input);
    panelRevision.current++;
    setPanel(null);
    setPanelMessage("");
    setConfirmDelete(false);
  };
  const syncSubscriptions = async (version: number) => {
    setSubscriptionsSyncing(true);
    try {
      const state = await checkConnection();
      if (!state.authorized || version !== panelRevision.current) return;
      const next = await call<Connection>("connection.refresh_subscriptions");
      if (version === panelRevision.current) setConnection(next);
    } catch (e) {
      if (version === panelRevision.current) setPanelMessage(errorMessage(e));
    } finally {
      try {
        const next = await call<Preferences>("collection.preferences");
        if (version === panelRevision.current) setPrefs(next);
      } catch { /* Last known subscription list stays usable while offline. */ }
      if (version === panelRevision.current) setSubscriptionsSyncing(false);
    }
  };
  useEffect(() => {
    if (panel !== "channels") return;
    const version = panelRevision.current;
    const timer = setInterval(() => {
      void call<Preferences>("collection.preferences").then(next => {
        if (version === panelRevision.current) setPrefs(next);
      }).catch(() => {});
    }, 10000);
    return () => clearInterval(timer);
  }, [panel]);
  const openPanel = async (kind: NonNullable<typeof panel>, asset?: Asset) => {
    if (kind !== "document") modal.begin();
    const version = ++panelRevision.current;
    setPanel(kind);
    setPanelMessage("");
    setPanelLoading(true);
    setPanelReady(false);
    setSelected(null);
    setSummaryLoading(false);
    setSummaryError("");
    setConfirmDelete(false);
    try {
      if (kind === "channels") {
        const value = await preferences();
        if (version === panelRevision.current) {
          setExcluded(value.excluded_channels);
          setChannelSearch("");
          void syncSubscriptions(version);
        }
      }
      if (kind === "connection") {
        await checkConnection();
      }
      if (kind === "document" && asset) {
        const value = await call<Inspection>("library.inspect", {
          video_id: asset.video_id,
        });
        if (version === panelRevision.current) {
          setSelected(value);
          if (!value.summary && value.manuscript_version) {
            setSummaryLoading(true);
            void call<{summary: string}>("summary.ensure", { video_id: asset.video_id })
              .then((result) => {
                if (version === panelRevision.current) {
                  setSelected((current) => current?.video_id === asset.video_id
                    ? { ...current, summary: result.summary } : current);
                }
              })
              .catch((error) => {
                if (version === panelRevision.current) setSummaryError(errorMessage(error));
              })
              .finally(() => {
                if (version === panelRevision.current) setSummaryLoading(false);
              });
          }
        }
      }
      if (version === panelRevision.current) setPanelReady(true);
    } catch (e) {
      if (version === panelRevision.current) setPanelMessage(errorMessage(e));
    } finally {
      if (version === panelRevision.current) setPanelLoading(false);
    }
  };
  const panelAction = async (work: (input: ModalInput) => Promise<void>) => {
    const input = modal.input();
    setBusy(true);
    setPanelMessage("");
    try {
      await work(input);
    } catch (e) {
      setPanelMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const connectAction = (capability: string, args = {}) =>
    panelAction(async () => {
      connectionEpoch.current++;
      connectionRequest.current = null;
      setConnectionChecking(false);
      setConnectionError(false);
      if (connection?.managed_content && capability === "connection.disconnect") {
        setPanelMessage(yt("正在断开 YouTube…"));
        const result = await call<Connection>(capability, args);
        setConnection(result);
        setContentRevision(value => value + 1);
        setPanelMessage("");
      } else {
        setPanelMessage(
          capability === "connection.configure"
            ? yt("正在保存到服务器凭据存储…")
            : capability === "connection.migrate_credentials" ? yt("正在迁移现有连接，请完成这一次 Vault 授权…") : yt("请在浏览器中完成 Google 授权…"),
        );
        const result = await call<Connection>(capability, args);
        setConnection(result);
        setClientSecret("");
        setPanelMessage(
          capability === "connection.authorize"
            ? yt("YouTube 已连接，已导入 {{value1}} 个订阅。", { value1: result.subscription_count })
            : capability === "connection.migrate_credentials"
              ? yt("连接已迁移到此 Edison 实例的凭据存储。")
            : capability === "connection.disconnect"
              ? yt("YouTube 授权已断开，已有文档仍保留。")
              : yt("客户端已保存，请继续在浏览器中授权 YouTube。"),
        );
      }
      await preferences();
    });
  const selectLayout = (value: Layout) => {
    setLayout(value);
    try {
      localStorage.setItem(layoutKey, value);
    } catch {
      /* Private browsing does not block the switch. */
    }
  };
  const sourcesById = new Map<string, Source>(prefs.sources.map((source) => [source.channel_id, source]));
  for (const asset of assets) {
    sourcesById.set(asset.channel_id, {
      ...sourcesById.get(asset.channel_id),
      channel_id: asset.channel_id,
      title: asset.channel_title,
    });
  }
  const sources = [...sourcesById.values()];
  const status = activity?.cutover?.held ? yt("YouTube 切换暂停") : activity?.drain_paused
    ? yt("更新已暂停")
    : activity && !activity.model_ready
      ? yt("等待模型设置")
      : prefs.sources.length
        ? yt("自动更新中")
        : yt("尚未连接");
  const clearFilters = () => {
    setQuery("");
    setPeriod("all");
    setChannel("");
    setUnread(false);
    setAfter("");
    setBefore("");
    setMessage("");
  };
  const [requestedVideo, setRequestedVideo] = useState<string | null>(null);
  const [requestedDocument, setRequestedDocument] = useState<Asset | null>(null);
  const openRequestedDocument = (asset: Asset) => { clearFilters(); setRequestedDocument(asset); setPage("documents"); };
  const acceptedVideo = (result: VideoRequest) => {
    if (!result.asset) return;
    if (result.disposition === "document") openRequestedDocument(result.asset);
    else { setRequestedVideo(result.asset.video_id); setPage("progress"); void refreshActivity(); }
  };
  const selectedId = selected?.video_id;
  return (
    <main ref={mainRef} tabIndex={-1} {...modal.capture} className={`yp-main${page === "documents" ? " yp-documents" : ""}`} aria-label={yt("YouTube 资料库")}>
      <NoticeStack messages={[message, progressMessage, panel ? "" : panelMessage, activity?.discovery_error || ""].map(value => value ? yt(value) : "")} />
      <CapabilityNotice health={activity?.health} />
      {activity?.cutover?.held && <section aria-label={yt("YouTube 切换暂停")} className="yp-maintenance" role="region">
        <strong>{yt(activity.cutover.state === "aborted" ? "切换已中止 · 采集暂停" : "YouTube 切换暂停")}</strong>
        <p>{yt(activity.cutover.reason || "")}</p>
        <p>{yt("Auto Update 的选择与已接收请求保持不变；仍可阅读、取消处理或断开 YouTube。")}</p>
        <p>{yt(activity.cutover.migration?.stage_label || "尚未迁移")}</p>
        {(activity.cutover.migration?.unknown.length ?? 0) > 0 && <>
          <p>{yt("待核对 {{count}} 项", {count: activity.cutover.migration?.unknown.length})}</p>
          <ul>{activity.cutover.migration?.unknown.map(item => <li key={item.video_id}>{item.title}</li>)}</ul>
        </>}
        {activity.cutover.state !== "aborted" && activity.cutover.deadline_at && <p>{yt("本次尝试截止：{{deadline}}", {deadline: new Date(activity.cutover.deadline_at * 1000).toLocaleString()})}</p>}
        <button className="yp-btn" onClick={() => void call("cutover.migrate").then(refreshActivity).catch(error => setMessage(String(error)))}>{yt("执行历史迁移")}</button>
        {activity.cutover.state !== "aborted" && <button className="yp-btn" onClick={() => void call("cutover.abort").then(refreshActivity).catch(error => setMessage(String(error)))}>{yt("中止切换并保持采集暂停")}</button>}
      </section>}
      <header className="yp-head">
        <div className="yp-header">
          <nav className="yp-page-nav" aria-label={yt("YouTube 页面")}>
            <button aria-current={page === "progress" ? "page" : undefined} onClick={() => setPage("progress")}>{yt("处理进度")}</button>
            <button aria-current={page === "documents" ? "page" : undefined} onClick={() => setPage("documents")}>{yt("阅读文档")}</button>
            <button aria-current={page === "activity" ? "page" : undefined} onClick={() => setPage("activity")}>{yt("事件时间线")}</button>
            <button aria-current={page === "add" ? "page" : undefined} onClick={() => setPage("add")}>{yt("添加视频")}</button>
          </nav>
          <div className="yp-tools">
            {!activity?.cutover?.held && <details className="yp-maintenance-tools">
              <summary>{yt("维护工具")}</summary>
              <p>{yt("切换暂停会等待在途成果保存，并暂停新的采集与连接变更。重启不会自动恢复采集。")}</p>
              <button className="yp-btn" onClick={() => void call("cutover.begin").then(refreshActivity).catch(error => setMessage(String(error)))}>{yt("开始切换暂停")}</button>
              <AcceptancePanel acceptance={activity?.acceptance} onChange={refreshActivity} onError={setMessage} />
            </details>}
            {page === "progress" && <div className="yp-progress-actions" ref={setProgressActions} />}
            <span className="yp-sync" role="status">
              <span className={`yp-dot ${activity?.drain_paused || activity?.cutover?.held ? "pending" : ""}`} />{status}
            </span>
            <button
              className="yp-iconbtn yp-labeled"
              onClick={() => void openPanel("channels")}
            >
              <Icon name="sidebar" />
              {yt("订阅频道")}</button>
            <button
              className="yp-iconbtn yp-labeled"
              onClick={onTranslationSettings}
            >
              <Icon name="pencil" />
              {yt("生成规则")}</button>
          </div>
        </div>
        {page === "documents" && <>
        <div className="yp-subline">
          <span>
            {
              prefs.sources.filter(
                (s) => !prefs.excluded_channels.includes(s.channel_id),
              ).length
            }{" "}
            {yt("个频道")}{prefs.excluded_channels.length
              ? yt(" · 已排除 {{value1}} 个", { value1: prefs.excluded_channels.length })
              : ""}
          </span>
        </div>
        <div className="yp-filterbar">
          <label className="yp-search">
            <Icon name="search" />
            <input
              type="search"
              aria-label={yt("搜索文档")}
              placeholder={yt("搜索文档")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <select
            aria-label={yt("按时间筛选")}
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setMessage("");
            }}
          >
            <option value="7">{yt("最近 7 天")}</option>
            <option value="1">{yt("今天")}</option>
            <option value="30">{yt("最近 30 天")}</option>
            <option value="all">{yt("全部时间")}</option>
            <option value="custom">{yt("自定时间")}</option>
          </select>
          {layout !== "channels" && (
            <select
              aria-label={yt("按频道筛选")}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="">{yt("全部频道")}</option>
              {sources.map((s) => (
                <option key={s.channel_id} value={s.channel_id}>
                  {s.title}
                </option>
              ))}
            </select>
          )}
          <select
            aria-label={yt("阅读筛选")}
            value={unread ? "unread" : "all"}
            onChange={(e) => setUnread(e.target.value === "unread")}
          >
            <option value="all">{yt("全部文档")}</option>
            <option value="unread">{yt("未读文档")}</option>
          </select>
          <span className="yp-count">{assets.length} {yt("篇")}</span>
          <div className="yp-layouts" role="group" aria-label={yt("呈现形式")}>
            {layouts.map(([value, label]) => (
              <button
                key={value}
                aria-label={label}
                title={label}
                aria-pressed={layout === value}
                onClick={() => selectLayout(value)}
              >
                <LayoutIcon kind={value} />
              </button>
            ))}
          </div>
        </div>
        {period === "custom" && (
          <div className="yp-filterbar" style={{ paddingTop: 12 }}>
            <label>
              {yt("从")}{" "}
              <input
                className="yp-time-input"
                type="date"
                aria-label={yt("起始日期")}
                value={after}
                onChange={(e) => {
                  setAfter(e.target.value);
                  setMessage("");
                }}
              />
            </label>
            <label>
              {yt("至")}{" "}
              <input
                className="yp-time-input"
                type="date"
                aria-label={yt("结束日期")}
                value={before}
                onChange={(e) => {
                  setBefore(e.target.value);
                  setMessage("");
                }}
              />
            </label>
          </div>
        )}
        </>}
      </header>
      <div className="yp-content">
        {page === "add" ? <AddVideo onAccepted={acceptedVideo} /> : page === "progress" ? (<>
          {requestedVideo && <RequestedVideo videoId={requestedVideo} onDocument={openRequestedDocument} />}
          <ProgressPage actionsTarget={progressActions} activity={activity} receivedAt={activityReceivedAt} disconnected={activityError}
            onRefresh={refreshActivity} onModelSettings={onModelSettings} onNotice={setProgressMessage} />
        </>) : page === "activity" ? (
          <ActivityPage activity={activity} disconnected={activityError} />
        ) : requestedDocument ? <RequestedDocument key={requestedDocument.video_id} asset={requestedDocument} onClose={() => setRequestedDocument(null)} /> : loading ? (
          <p className="yp-inline-status">{yt("正在加载文档…")}</p>
        ) : !assets.length && layout !== "channels" ? (
          <div className="yp-empty">
            <Icon name="file" size={28} />
            <h2>
              {prefs.sources.length
                ? yt("没有找到文档")
                : yt("把订阅更新，变成阅读文档")}
            </h2>
            <p>
              {prefs.sources.length
                ? yt("可以调整筛选，或查看自动更新的准备进度。")
                : yt("连接 YouTube 后，自动处理你订阅的频道。")}
            </p>
            <button
              className="yp-btn"
              onClick={
                prefs.sources.length
                  ? clearFilters
                  : () => void openPanel("connection")
              }
            >
              {prefs.sources.length
                ? yt("清除筛选")
                : connection?.authorized
                  ? yt("已连接 YouTube")
                  : yt("连接 YouTube")}
            </button>
          </div>
        ) : (
          <DocumentViews
            layout={layout}
            assets={assets}
            sources={sources}
            channel={channel}
            onChannel={setChannel}
            onSelect={(a) => { if (!busy) void openPanel("document", a); }}
            selectedId={panel === "document" ? selectedId : undefined}
          />
        )}
      </div>

      {page === "documents" && <aside className="yp-inspector" aria-label={yt("文档信息")}>
        <header className="yp-inspector-head"><span>{yt("文档信息")}</span>
          {panel === "document" && <button className="yp-iconbtn" aria-label={yt("关闭文档信息")} onClick={() => close()}><Icon name="x" /></button>}
        </header>
        <div className="yp-inspector-body">
          {panelMessage && panel === "document" && <p role="status" className="yp-inline-note">{yt(panelMessage)}</p>}
          {panel === "document" && (<>
          {panelLoading ? (
            <p>{yt("正在读取文档信息…")}</p>
          ) : (
            selected && (
              <>
                <h3>{selected.title}</h3>
                <div className="yp-inspector-author"><ChannelAvatar name={selected.channel_title} thumbnailUrl={sourcesById.get(selected.channel_id)?.thumbnail_url} /><span>{selected.channel_title}</span></div>
                <section className="yp-inspector-summary" aria-live="polite">
                  <h4>{yt("摘要")}</h4>
                  <p>{selected.summary || (summaryLoading ? yt("正在生成摘要…")
                    : summaryError || yt("摘要暂不可用，请稍后重新打开文档。"))}</p>
                </section>
                <h4>{yt("元数据")}</h4>
                <dl>
                  <dt>{yt("阅读时间")}</dt><dd>{readingTime(selected) || "—"}</dd>
                  <dt>{yt("发布日期")}</dt>
                  <dd>{publicationLabel(selected.published_at)}</dd>
                  <dt>{yt("视频时长")}</dt>
                  <dd>{videoDurationLabel(selected.duration_seconds)}</dd>
                  <dt>{yt("来源字幕")}</dt>
                  <dd>
                    {selected.source_trace.transcript_available
                      ? yt("已保留时间戳")
                      : yt("暂无")}
                  </dd>
                  <dt>{yt("原始视频")}</dt>
                  <dd>
                    <button
                      className="yp-link"
                      disabled={busy}
                      onClick={() =>
                        void panelAction(async () => {
                          setPanelMessage(yt("正在打开浏览器…"));
                        await call("sources.open", { video_id: selectedId });
                          setPanelMessage(yt("已在默认浏览器中打开原始视频。"));
                        })
                      }
                    >
                      {yt("打开 YouTube")}</button>
                  </dd>
                </dl>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  <button
                    className="yp-btn primary"
                    disabled={busy}
                    onClick={() =>
                      void panelAction(async () => {
                        await call("documents.open", { video_id: selectedId });
                        setPanelMessage(yt("已交给默认应用打开。"));
                      })
                    }
                  >
                    {yt("用默认应用打开")}</button>
                  {selected.source_trace.translation_available && <button
                    className="yp-btn" disabled={busy} onClick={() => void panelAction(async () => {
                      await call("documents.open_translation", { video_id: selectedId });
                      setPanelMessage(yt("已打开完整译文。"));
                    })}>{yt("查看完整译文")}</button>}
                  <button
                    className="yp-btn"
                    disabled={busy}
                    onClick={() =>
                      void panelAction(async () => {
                        const d = await call<{ markdown: string }>(
                          "documents.get",
                          { video_id: selectedId },
                        );
                        await navigator.clipboard.writeText(d.markdown);
                        setPanelMessage(yt("Markdown 已复制。"));
                      })
                    }
                  >
                    <Icon name="copy" />
                    {yt("复制正文")}</button>
                </div>
                <div style={{ display: "flex", gap: 16, marginTop: 22 }}>
                  <button
                    className="yp-link"
                    disabled={
                      busy ||
                      ["queued", "acquiring", "generating"].includes(
                        selected.preparation_state,
                      )
                    }
                    onClick={() =>
                      void panelAction(async () => {
                        setSelected(
                          await call<Inspection>("library.regenerate", {
                            video_id: selectedId,
                          }),
                        );
                        setPanelMessage(yt("已排队生成新版本，旧版本仍保留。"));
                        await refresh();
                      })
                    }
                  >
                    {yt("生成新版本")}</button>
                  <button
                    className="yp-link"
                    disabled={busy}
                    onClick={() => setConfirmDelete(true)}
                  >
                    {yt("删除文档")}</button>
                </div>
                {confirmDelete && (
                  <div className="yp-inline-note" style={{ marginTop: 16 }}>
                    {yt("删除这份资料及全部稿件和字幕？")}<div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                      <button
                        className="yp-btn"
                        onClick={() => setConfirmDelete(false)}
                      >
                        {yt("取消")}</button>
                      <button
                        className="yp-btn"
                        disabled={busy}
                        onClick={() =>
                          void panelAction(async (input) => {
                            await call("library.delete", {
                              video_id: selectedId,
                            });
                            close(input);
                            await refresh();
                          })
                        }
                      >
                        {yt("确认删除")}</button>
                    </div>
                  </div>
                )}
              </>
            )
          )}
        </>)}
          {panel !== "document" && <p className="yp-inspector-placeholder">{yt("选择文档，查看摘要与详细信息。")}</p>}
        </div>
        {panel === "document" && <footer className="yp-inspector-footer">{
          selected && !panelLoading ? (
              <>
                <span>
                  {selected.reading_state === "read" ? yt("已读") : yt("未读")} · v
                  {selected.manuscript_version}
                </span>
                <button
                  className="yp-link"
                  disabled={busy}
                  onClick={() =>
                    void panelAction(async () => {
                      setSelected(
                        await call<Inspection>("library.set_reading_state", {
                          video_id: selectedId,
                          reading_state:
                            selected.reading_state === "read"
                              ? "inbox"
                              : "read",
                        }),
                      );
                      await refresh();
                    })
                  }
                >
                  {selected.reading_state === "read" ? yt("标为未读") : yt("标为已读")}
                </button>
              </>
            ) : null}</footer>}
      </aside>}
      <Dialog
        wide={panel === "connection"}
        open={panel === "channels" || panel === "connection"} interaction={modal} fallbackFocusRef={mainRef}
        title={panel === "channels" ? yt("订阅频道") : panel === "connection" ? yt("连接 YouTube") : yt("文档信息")}
        subtitle={panel === "channels" ? yt("默认自动生成所有订阅频道的阅读文档。") : panel === "connection" ? yt("分别完成官方订阅授权与服务器内容登录；连接保存在此 Edison 实例。") : selected?.channel_title}
        onClose={close}
        footer={panel === "channels" ? (
            <>
              <span>
                {
                  prefs.sources.filter((s) => !excluded.includes(s.channel_id))
                    .length
                }{" "}
                {yt("个频道已开启")}</span>
              <div>
                <button className="yp-btn" onClick={() => close()}>
                  {yt("取消")}</button>{" "}
                <button
                  className="yp-btn primary"
                  disabled={busy || panelLoading || !panelReady}
                  onClick={() =>
                    void panelAction(async (input) => {
                      setPrefs(
                        await call<Preferences>("collection.set_exclusions", {
                          excluded_channels: excluded,
                        }),
                      );
                      close(input);
                      setMessage(yt("频道设置已保存。"));
                      await refresh();
                    })
                  }
                >
                  {yt("保存")}</button>
              </div>
            </>
        ) : null}
      >
        {panelMessage && <p role="status" className="yp-inline-note">{yt(panelMessage)}</p>}
        {panel === "channels" && (<>
          <div className="yp-inline-note">
            {yt("关闭频道后，不再领取它的新文档；正在生成的文档会完成，已有文档仍保留。新导入的订阅默认开启。")}</div>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <button
              className="yp-btn"
              onClick={() => void openPanel("connection")}
            >
              {connection?.authorized && (
                <span className="yp-dot" aria-hidden="true" />
              )}
              {connection?.authorized ? yt("已连接 YouTube") : yt("连接 YouTube")}
            </button>
            <span
              role="status"
              className="yp-inline-status"
              style={{ padding: "7px 0" }}
            >
              {connectionChecking
                ? yt("正在确认连接状态…")
                : connectionError
                  ? yt("连接状态未知，请重试")
                  : connection?.authorized
                    ? yt("{{value1}} 个订阅", { value1: connection.subscription_count })
                    : connection
                      ? yt("未连接")
                      : ""}
            </span>
            <button className="yp-btn" disabled={busy || subscriptionsSyncing || !connection?.authorized}
              onClick={() => void syncSubscriptions(panelRevision.current)}>
              {subscriptionsSyncing ? yt("正在同步订阅…") : yt("同步订阅")}</button>
          </div>
          <p className="yp-inline-status" role="status">
            {prefs.last_synced_at ? yt("上次同步：{{value1}}", { value1: new Date(prefs.last_synced_at * 1000).toLocaleString() }) : yt("尚未同步账号订阅")}
            {prefs.reconnect_required ? ` · ${yt("授权已失效，请重新连接 YouTube。")}` : prefs.sync_error ? ` · ${yt("同步暂时失败，继续显示上次结果。")}` : ""}
          </p>
          <label className="yp-search">
            <Icon name="search" />
            <input
              aria-label={yt("搜索订阅频道")}
              placeholder={yt("搜索订阅频道")}
              value={channelSearch}
              onChange={(e) => setChannelSearch(e.target.value)}
            />
          </label>
          <div className="yp-channel-actions">
            <span className="yp-inline-status">{yt("全部")}{prefs.sources.length} {yt("个频道")}</span>
            <button
              className="yp-btn"
              disabled={busy || panelLoading || !panelReady || !prefs.sources.length}
              onClick={() => setExcluded((current) => current.filter(
                (id) => !prefs.sources.some((source) => source.channel_id === id),
              ))}
            >
              {yt("全选")}</button>
            <button
              className="yp-btn"
              disabled={busy || panelLoading || !panelReady || !prefs.sources.length}
              onClick={() => setExcluded((current) => [...new Set([
                ...current, ...prefs.sources.map((source) => source.channel_id),
              ])])}
            >
              {yt("全不选")}</button>
          </div>
          {panelLoading ? (
            <p>{yt("正在读取频道…")}</p>
          ) : (
            prefs.sources
              .filter((s) =>
                s.title.toLowerCase().includes(channelSearch.toLowerCase()),
              )
              .map((s) => (
                <div className="yp-channel-row" key={s.channel_id}>
                  <ChannelAvatar name={s.title} thumbnailUrl={s.thumbnail_url} />
                  <div>
                    <strong>{s.title}</strong>
                  </div>
                  <label className="yp-switch">
                    <input
                      type="checkbox"
                      aria-label={yt("自动生成 {{value1}}", { value1: s.title })}
                      checked={!excluded.includes(s.channel_id)}
                      onChange={(e) =>
                        setExcluded(
                          e.target.checked
                            ? excluded.filter((x) => x !== s.channel_id)
                            : [...excluded, s.channel_id],
                        )
                      }
                    />
                    <span />
                  </label>
                </div>
              ))
          )}
          {!panelLoading && !prefs.sources.length && (
            <p className="yp-inline-status">{yt("尚未导入订阅。请先连接 YouTube。")}</p>
          )}
        </>)}
        {panel === "connection" && (<>
          {connection && <OfficialAuthorization connection={connection} onChange={value => { setConnection(value); void preferences(); }} />}
          <YouTubeSessionSettings key={contentRevision} />
          {connection?.managed_content ? (
            <section aria-label={yt("断开 YouTube")}>
              <p>{yt("断开会同时停止本实例使用 Google 官方授权和 YouTube 内容会话；已有资料、阅读记录与 Auto Update 选择保留。")}</p>
              <button className="yp-btn" disabled={busy} onClick={() => void connectAction("connection.disconnect")}>
                {yt("断开 YouTube")}
              </button>
              {connection.disconnection?.local === "cleared" && <p role="status">{yt("本实例已清除 Google 官方授权与 YouTube 内容会话。")}</p>}
              {connection.disconnection?.local === "failed" && <p role="alert">{yt("本实例已停止使用 YouTube，但凭据清理未完成。请检查服务器存储并再次断开。")}</p>}
              {connection.disconnection?.google === "revoked" && <p>{yt("Google 平台授权已撤销。")}</p>}
              {connection.disconnection?.google === "failed" && <p role="alert">{yt("Google 平台撤销失败；本实例已停止使用，已有资料仍可阅读。")}</p>}
              {["not_available", "unknown"].includes(connection.disconnection?.google || "") && <p>{yt("没有可用于撤销的 Google 授权，平台撤销结果尚未确认。")}</p>}
            </section>
          ) : (
            <>
          {panelLoading ? (
            <p>{yt("正在检查连接状态…")}</p>
          ) : connectionError || !connection ? (
            <div>
              <p>{yt("连接状态未知，请重试")}</p>
              <button className="yp-btn" onClick={() => void openPanel("connection")}>{yt("重试")}</button>
            </div>
          ) : !connection.configured ? (
            <>
            <p>{yt("以下设置仅用于 Google 官方授权（订阅读取），不控制上方的 YouTube 内容登录。")}</p>
            <button className="yp-btn primary" disabled={busy} onClick={() => void connectAction("connection.migrate_credentials")}>{yt("迁移现有连接")}</button>
            <details style={{ marginTop: 16 }}><summary>{yt("开发者设置")}</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void connectAction("connection.configure", {
                  client_id: clientId,
                  client_secret: clientSecret,
                });
              }}
            >
              <label>
                Client ID
                <input
                  className="yp-modal-field"
                  required
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                />
              </label>
              <label>
                Client Secret
                <input
                  className="yp-modal-field"
                  required
                  type="password"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                />
              </label>
              <button
                className="yp-btn primary"
                style={{ marginTop: 16 }}
                disabled={busy}
              >
                {busy ? yt("正在保存…") : yt("保存官方授权配置")}
              </button>
            </form>
            </details>
            </>
          ) : !connection.authorized ? (
            <button
              className="yp-btn primary"
              disabled={busy}
              onClick={() => void connectAction("connection.authorize")}
            >
              {yt("在浏览器中授权并导入订阅")}</button>
          ) : (
            <>
              <p>{yt("已连接 ·")}{connection.subscription_count} {yt("个订阅")}</p>
              <button
                className="yp-btn"
                disabled={busy}
                onClick={() => void connectAction("connection.disconnect")}
              >
                {yt("断开授权")}</button>
            </>
          )}
            </>
          )}
          <p className="yp-inline-status">
            {yt("测试模式下，请将登录邮箱加入 Google Cloud 的测试用户名单。")}</p>
        </>)}

      </Dialog>
    </main>
  );
}
function LayoutIcon({ kind }: { kind: Layout }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      aria-hidden="true"
    >
      {kind === "timeline" ? (
        <>
          <path d="M6 4h11M6 10h11M6 16h11" />
          <circle cx="2.5" cy="4" r=".8" />
          <circle cx="2.5" cy="10" r=".8" />
          <circle cx="2.5" cy="16" r=".8" />
        </>
      ) : kind === "library" ? (
        <>
          <rect x="2" y="2" width="6" height="6" rx="1" />
          <rect x="12" y="2" width="6" height="6" rx="1" />
          <rect x="2" y="12" width="6" height="6" rx="1" />
          <rect x="12" y="12" width="6" height="6" rx="1" />
        </>
      ) : (
        <>
          <rect x="2" y="2" width="16" height="16" rx="2" />
          <path d="M8 2v16M11 6h4M11 10h4M11 14h4" />
        </>
      )}
    </svg>
  );
}
