import { createPortal } from "react-dom";
import { Icon } from "../Icon";
import { useTranslation } from "react-i18next";
import { yt } from "./text";
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { youtubeCapability as call } from "../../api";
import type { Activity, QueueItem } from "./types";
import { videoMetadataText } from "./types";
import { LaunchHero, type Milestone } from "./discovery/LaunchHero";
import { activityLabels } from "./activityLabels";
import { RawConsole } from "./RawConsole";
import { DiscoveryStatus } from "./DiscoveryStatus";
import { CapabilityHealth } from "./CapabilityHealth";
import "./discovery/discovery.css";
import "./discovery/hierarchy.css";
import "./progress.css";

type QueueState = "waiting_retry" | "awaiting_completion" | "expired" | "awaiting_timing" | "queued" | "rate_limited" | "awaiting_classification" | "filtered" | "cancelled" | "failed" | "unavailable";
type Page = { items: QueueItem[]; total: number };
function ago(timestamp: number | null | undefined, now: number) {
  if (!timestamp) return yt("尚无记录");
  const seconds = Math.max(0, Math.floor((now - timestamp * 1000) / 1000));
  return seconds < 60 ? yt("{{value1}} 秒前", { value1: seconds }) : yt("{{value1}} 分钟前", { value1: Math.floor(seconds / 60) });
}
function failure(reason: string | null, kind?: string | null) {
  const categories: Record<string, string> = { private: "视频为私有", deleted: "视频已删除", access_denied: "没有访问权限", unknown: "获取原因待核实", caption_pending: "字幕暂未提供" };
  if (kind && categories[kind]) return {title: yt(categories[kind]), description: yt("已有资料保留，可展开查看原因。")};
  if (/429|too many requests|限流/i.test(reason || ""))
    return { title: yt("YouTube 请求限流"), description: yt("获取请求暂缓，已有资料保留。") };
  if (/no subtitles|subtitle.*empty|没有字幕/i.test(reason || ""))
    return { title: yt("获取原因待核实"), description: yt("已有资料保留，可展开查看原因。") };
  if (/中断|interrupt/i.test(reason || ""))
    return { title: yt("上次处理被中断"), description: yt("已完成阶段保留，可以继续重试。") };
  return { title: yt("处理未完成"), description: yt("已有资料保留，可展开查看原因。") };
}

export function ProgressPage({ actionsTarget, activity, receivedAt, disconnected, onRefresh, onModelSettings, onNotice }: {
  actionsTarget: HTMLDivElement | null;
  activity: Activity | null; receivedAt: number; disconnected: boolean;
  onRefresh: () => Promise<void>; onModelSettings: () => void;
  onNotice: Dispatch<SetStateAction<string>>;
}) {
  useTranslation();
  const labels = activityLabels();
  const filters = [["queued", yt("待处理")], ["awaiting_completion", yt("等待视频结束")], ["expired", yt("已过期")], ["awaiting_timing", yt("待核实时间")], ["waiting_retry", yt("等待重试")], ["rate_limited", yt("限流等待")], ["awaiting_classification", yt("待确认类型")], ["filtered", yt("已排除 Shorts")], ["cancelled", yt("已取消")], ["failed", yt("处理失败")], ["unavailable", yt("暂不可用")]] as const;
  const [now, setNow] = useState(Date.now());
  const [filter, setFilter] = useState<QueueState>("queued");
  const [offset, setOffset] = useState(0);
  const [list, setList] = useState<Page>({ items: [], total: 0 });
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const setMessage = onNotice;
  useEffect(() => { if (listError) onNotice(listError); }, [listError, onNotice]);
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => { mounted.current = false; epoch.current++; clearInterval(timer); };
  }, []);
  const refreshList = useCallback(async () => {
    const version = ++epoch.current;
    try {
      const result = await call<Page>("activity.list", { state: filter, offset, limit: 50 });
      if (version !== epoch.current || !mounted.current) return;
      setList(result);
      setSelected(ids => ids.filter(id => result.items.some(item => item.video_id === id)));
      setListError("");
      if (!result.items.length && offset && result.total <= offset) setOffset(Math.max(0, offset - 50));
    } catch (e) {
      if (version === epoch.current && mounted.current) setListError(e instanceof Error ? e.message : String(e));
    } finally {
      if (version === epoch.current && mounted.current) setLoading(false);
    }
  }, [filter, offset]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setSelected([]);
    const update = async () => {
      await refreshList();
      if (alive) timer = setTimeout(update, 4000);
    };
    void update();
    return () => { alive = false; epoch.current++; clearTimeout(timer); };
  }, [refreshList]);

  const run = async (capability: string, args: Record<string, unknown> = {}) => {
    setBusy(true); setMessage("");
    try {
      const result = await call<{ changed?: string[]; skipped?: {video_id: string; state: string}[] }>(capability, args);
      if (!mounted.current) return;
      if (result.changed) {
        const verb = capability === "activity.restore" ? yt("恢复排队") : yt("取消");
        setMessage(yt("已{{value1}} {{value2}} 个视频。{{value3}}", { value1: verb, value2: result.changed.length, value3: result.skipped?.length ? yt("另有 {{value1}} 个状态已变化，未{{value2}}；请查看最新列表。", { value1: result.skipped.length, value2: verb }) : "" }));
        setSelected([]);
      } else setMessage(yt("操作已完成。"));
      try { await onRefresh(); } catch { setMessage(previous => previous + yt(" 状态暂时无法刷新，稍后自动重试。")); }
      await refreshList();
    } catch (e) {
      if (mounted.current) setMessage(e instanceof Error ? e.message : String(e));
    } finally { if (mounted.current) setBusy(false); }
  };
  const current = activity?.current?.[0];
  const stale = disconnected || !receivedAt || now - receivedAt > 12000;
  const heartbeat = activity?.runtime?.heartbeat_at;
  const live = !stale && !!activity?.runtime?.worker_alive && !!heartbeat && now - heartbeat * 1000 < 10000;
  const active = !!current;
  const cooldownUntil = activity?.youtube_requests?.cooldown_until || 0;
  const cooling = cooldownUntil * 1000 > now;
  const stage = current?.preparation_stage || current?.preparation_state;
  const title = stale ? yt("正在重新连接") : activity?.cutover?.held && !active ? yt("YouTube 切换暂停") : !activity?.model_ready ? yt("等待模型设置")
    : active ? labels[stage || ""] || yt("正在处理") : activity?.drain_paused ? yt("更新已暂停")
    : cooling ? yt("YouTube 冷却中")
    : activity?.runtime?.discovering ? yt("正在检查订阅") : activity?.queued ? yt("等待处理")
    : activity?.awaiting_classification ? yt("等待确认视频类型") : yt("等待新的订阅更新");
  const events = activity?.events || [];
  const jobEvents = events.filter(event => event.video_id === current?.video_id && event.occurred_at >= (current?.preparation_started_at || 0));
  const stages = ["checking", "acquiring", "initial", "review", "revision", "composition"];
  const milestones: Milestone[] = current ? stages.map((id, index) => {
    const observed = id === "acquiring" ? current.preparation_state === "generating" : (current.observed_stages?.includes(id) || jobEvents.some(event => event.stage === id));
    const done = observed && index < stages.indexOf(stage || "");
    return { id, label: labels[id], state: id === stage ? "active" : done ? "done" : "pending",
      summary: id === stage ? yt("进行中") : done ? yt("已完成") : yt("未执行 / 待处理") };
  }) : [];
  const canSelect = filter === "waiting_retry" || filter === "awaiting_completion" || filter === "expired" || filter === "awaiting_timing" || filter === "queued" || filter === "cancelled" || filter === "awaiting_classification" || filter === "rate_limited";
  const verb = filter === "cancelled" ? yt("恢复处理") : yt("取消处理");
  const capability = filter === "cancelled" ? "activity.restore" : "activity.cancel";

  return <section className="yp-progress ui-surface-hierarchy--ma" aria-label={yt("处理进度")}>
    <div className="yp-progress-heading">
      <span className={`yp-heartbeat ${live ? "is-live" : ""}`} role="status">
        {stale ? yt("连接中断 · 正在重连") : activity?.cutover?.held ? yt("维护中 · 采集暂停") : live ? yt("后台在线") : yt("后台心跳未确认")}
      </span>
    </div>
    <CapabilityHealth health={activity?.health} />
    <DiscoveryStatus discovery={activity?.discovery} paused={!!activity?.drain_paused} />
    <div className="discovery-stage-strip-layout-ma">
      <LaunchHero live={live && active} offline={stale || (!!activity?.runtime && !live && !activity?.cutover?.held)} observedStage={title}
        signalCopy={current ? `${current.title} · ${videoMetadataText(current)}` : ""}
        milestones={milestones} metrics={[
          { label: yt("待处理"), value: activity?.queued ?? "—", note: yt("尚未开始，可以取消") },
          { label: yt("本轮已处理"), value: activity?.batch.completed ?? "—", note: yt("包含成功与未完成的尝试") },
          { label: yt("已生成"), value: activity?.document_count ?? activity?.ready ?? "—", note: yt("资料库累计文档") },
          { label: yt("最近进展"), value: ago(current?.stage_updated_at || events[0]?.occurred_at, now), note: yt("后台心跳：{{value1}}", { value1: ago(heartbeat, now) }) },
        ]} />
    </div>
    {actionsTarget && createPortal(<>
      <button className="yp-iconbtn" title={activity?.drain_paused ? yt("恢复自动更新") : yt("完成当前文档后暂停")}
        aria-label={activity?.drain_paused ? yt("恢复自动更新") : yt("完成当前文档后暂停")}
        disabled={busy || !activity} onClick={() => void run(activity?.drain_paused ? "activity.resume" : "activity.drain_pause")}>
        <Icon name={activity?.drain_paused ? "play" : "pause"} />
      </button>
      <button className="yp-iconbtn" title={yt("立即检查更新")} aria-label={yt("立即检查更新")}
        disabled={busy || !activity} onClick={() => void run("collection.refresh_updates")}><Icon name="refresh" /></button>
      {!!activity?.failed && <button className="yp-iconbtn" title={yt("重试失败项")} aria-label={yt("重试失败项")}
        disabled={busy} onClick={() => void run("activity.retry_all_failed")}><Icon name="retry" /></button>}
    </>, actionsTarget)}
    {activity && !activity.model_ready && <button className="yp-btn" onClick={onModelSettings}>{yt("连接生成模型")}</button>}
    {activity?.drain_paused && active && <p className="yp-progress-note">{yt("自动更新已暂停；已接收的手动请求仍可继续。")}</p>}
    {activity?.resources?.captions_waiting && <p className="yp-progress-notice" role="status">{activity.resources.reason}</p>}
    {!!activity?.youtube_requests?.reason && <p className="yp-progress-notice" role="status">{activity.youtube_requests.reason}</p>}
    {cooling && <p className="yp-progress-notice" role="status">{yt("YouTube 获取请求已暂停，预计")}{new Date(cooldownUntil * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})} {yt("后再尝试（约")}{Math.ceil((cooldownUntil * 1000 - now) / 60000)} {yt("分钟）。已有文稿可以继续阅读；重启或手动恢复不会提前解除冷却。")}</p>}

    <section className="yp-queue" aria-label={yt("处理队列")}>
      <div className="yp-queue-head">
        <div className="yp-queue-filters" role="group" aria-label={yt("处理状态")}>
          {filters.map(([key, label]) => <button key={key} disabled={busy} aria-pressed={filter === key} onClick={() => { setFilter(key); setOffset(0); }}>
            {label} <span>{activity?.[key] ?? 0}</span>
          </button>)}
        </div>
        {canSelect && <button className="yp-btn" disabled={busy || !selected.length || loading || !!listError}
          onClick={() => void run(capability, {video_ids: selected})}>{verb}{yt("所选")}{selected.length ? `（${selected.length}）` : ""}</button>}
      </div>
      {filter === "cancelled" && <p className="yp-progress-note">{yt("自动更新不会重新加入这些视频。恢复后重新排队，已有资料始终保留。")}</p>}
      {filter === "awaiting_classification" && <p className="yp-progress-note">{yt("稍后自动重试；确认前不会获取字幕或生成文稿。")}</p>}
      {filter === "rate_limited" && <p className="yp-progress-note">{yt("冷却结束后自动探测恢复，也可以取消处理。持续限流时每六小时共享检查一次，不消耗各视频的重试预算。")}</p>}
      {filter === "filtered" && <p className="yp-progress-note">{yt("这些视频被 YouTube 归类为 Shorts，不会获取字幕或生成文稿。已有文稿保留。")}</p>}
      {loading ? <p className="yp-progress-note">{yt("正在加载队列…")}</p> : <>
        {canSelect && !!list.items.length && <label className="yp-select-page"><input type="checkbox" aria-label={yt("选择本页全部视频")}
          checked={!!list.items.length && selected.length === list.items.length} disabled={busy || !!listError}
          onChange={e => setSelected(e.target.checked ? list.items.map(item => item.video_id) : [])} />{yt("选择本页 ·")}{list.total} {yt("个视频")}</label>}
        <ul className="yp-queue-list">
          {list.items.map(item => {
            const reason = failure(item.failure_reason, item.acquisition_failure_kind);
            return <li key={item.video_id}>
              {canSelect && <input type="checkbox" aria-label={yt("选择 {{value1}}", { value1: item.title })} disabled={busy || !!listError}
                checked={selected.includes(item.video_id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, item.video_id] : ids.filter(id => id !== item.video_id))} />}
              <div className="yp-queue-copy"><strong>{item.title}</strong><span>{videoMetadataText(item)}</span>
                {item.retry_at && <p>{yt("下一次检查")}：{new Date(item.retry_at * 1000).toLocaleString()}</p>}
                {(filter === "waiting_retry" || !!item.caption_checks_ended_at) && <p>{item.failure_reason}</p>}
                <p>{item.request_kind === "manual" ? yt("手动请求") : item.commenced_at ? yt("已实际开工，可跨窗口续办；仍需自动更新许可。") : yt("尚未实际开工，首次开工仍须在 72 小时内。")}</p>
                {(filter === "awaiting_completion" || filter === "expired" || filter === "awaiting_timing") && <p>{yt(item.failure_reason || "")}</p>}
                {filter !== "awaiting_completion" && filter !== "expired" && filter !== "awaiting_timing" && !!item.failure_reason && !item.caption_checks_ended_at && <><p>{reason.title} · {reason.description}</p><details><summary>{yt("技术详情")}</summary><pre>{item.failure_reason}</pre></details></>}
              </div>
              <div className="yp-queue-actions">
                {canSelect && <button className="yp-btn" disabled={busy || !!listError} onClick={() => void run(capability, {video_ids: [item.video_id]})}>{verb}</button>}
                {item.preparation_state === "failed" && <>
                  <button className="yp-btn" disabled={busy} onClick={() => void run("activity.retry", {video_id: item.video_id})}>{yt("继续重试")}</button>
                  <button className="yp-link" disabled={busy} onClick={() => void run("library.regenerate", {video_id: item.video_id})}>{yt("重新生成")}</button>
                </>}
              </div>
            </li>;
          })}
        </ul>
        {!list.items.length && !listError && <p className="yp-queue-empty">{filter === "queued" ? yt("没有等待处理的视频。") : yt("没有{{value1}}的视频。", { value1: filters.find(([key]) => key === filter)?.[1] })}</p>}
        {list.total > 50 && <div className="yp-pagination">
          <button className="yp-btn" disabled={!offset || busy} onClick={() => setOffset(value => Math.max(0, value - 50))}>{yt("上一页")}</button>
          <span>{offset + 1}–{Math.min(offset + 50, list.total)} / {list.total}</span>
          <button className="yp-btn" disabled={offset + 50 >= list.total || busy} onClick={() => setOffset(value => value + 50)}>{yt("下一页")}</button>
        </div>}
      </>}
    </section>
    <RawConsole entries={activity?.console || []} stale={stale} now={now} />
  </section>;
}
