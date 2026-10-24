import { useEffect, useRef, useState } from "react";
import { YouTubeCapabilityError, youtubeCapability, youtubeValidationCapability } from "../api";

export type SessionMaintenance = {
  state: "idle" | "due" | "running" | "waiting_budget" | "waiting_resources" | "waiting_cooldown" | "handoff";
  basis: "signed_out" | "rotation" | null; reason: string | null;
  next_at: number | null; deadline_at: number | null; used: number; limit: number;
};
export type ContentSessionStatus = {
  state: "disconnected" | "unverified" | "available" | "needs_user" | "needs_maintenance" | "interrupted" | "waiting_resources" | "maintaining";
  browser_pending?: boolean;
  browser_running: boolean; snapshot_present: boolean; reason?: string;
  account_consistency?: string; last_success_at?: number; idle_closes_at?: number;
  maintenance?: SessionMaintenance;
};
const time = (seconds: number) => new Date(seconds * 1000).toLocaleString();
type Screen = { image: string; width: number; height: number };
const button = "rounded-lg border border-line px-3 py-2 text-[13px] hover:bg-panel disabled:opacity-40";
const reconnect = "无法连接 Edison 服务器。请检查网络；若通过 SSH 隧道访问，请先恢复隧道，再重试连接。尚未收到的操作结果暂不可确认，这不代表 Google 登录失败。";

export function YouTubeSessionSettings({ maintenance: maintenanceMode = false }: {maintenance?: boolean} = {}) {
  const request = maintenanceMode ? youtubeValidationCapability : youtubeCapability;
  const [status, setStatus] = useState<ContentSessionStatus | null>(null);
  const [screen, setScreen] = useState<Screen | null>(null);
  const [sameAccount, setSameAccount] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [networkError, setNetworkError] = useState("");
  const [viewError, setViewError] = useState("");
  const [retryAction, setRetryAction] = useState<"status" | "open">("status");
  const [viewAttempt, setViewAttempt] = useState(0);
  const queued = useRef(Promise.resolve());
  const revision = useRef(0);
  const viewPaused = !!networkError || !!viewError;
  const changingBrowser = busy === "open" || busy === "confirm" || busy === "close";
  const maintenance = status?.maintenance;
  // The unattended run's browser is never a person's input view.
  const handoffBrowser = !!status?.browser_running && maintenance?.state !== "running";

  const failed = (failure: unknown, view: boolean, retry: "status" | "open") => {
    setRetryAction(retry);
    if (view || !(failure instanceof YouTubeCapabilityError)) {
      revision.current++;
      setScreen(null);
    }
    if (failure instanceof YouTubeCapabilityError) {
      if (view) setViewError(failure.message);
      else setError(failure.message);
    } else {
      setNetworkError((view ? "登录画面连接已中断，旧画面已隐藏。" : "") + reconnect);
    }
  };
  useEffect(() => {
    let active = true;
    const observed = revision.current;
    const current = () => active && observed === revision.current;
    void youtubeCapability<ContentSessionStatus>("content_session.status")
      .then(value => { if (current()) setStatus(value); })
      .catch(failure => { if (current()) failed(failure, false, "status"); });
    return () => { active = false; revision.current++; };
  }, []);
  useEffect(() => {
    if (!handoffBrowser || viewPaused || changingBrowser) { setScreen(null); return; }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const observed = revision.current;
    const current = () => active && observed === revision.current;
    const poll = async () => {
      try {
        const state = await youtubeCapability<ContentSessionStatus>("content_session.status");
        if (!current()) return;
        setStatus(state);
        if (!state.browser_running || state.maintenance?.state === "running") { setScreen(null); return; }
        const next = await request<Screen>("content_session.screen");
        if (!current()) return;
        setScreen(next);
        timer = setTimeout(poll, 1200);
      } catch (failure) {
        if (current()) failed(failure, true, "open");
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [handoffBrowser, viewPaused, changingBrowser, viewAttempt]);

  useEffect(() => {
    if (status?.state !== "waiting_resources" && status?.state !== "maintaining") return;
    const timer = setInterval(() => {
      const observed = revision.current;
      void youtubeCapability<ContentSessionStatus>("content_session.status").then(next => {
        if (observed !== revision.current) return;
        setStatus(next);
        if (next.state !== "waiting_resources" && next.state !== "maintaining") {setError(""); setViewError("");}
      }).catch(() => {});
    }, 1200);
    return () => clearInterval(timer);
  }, [status?.state]);

  const run = async (operation: string) => {
    setBusy(operation); setError("");
    if (["open", "confirm", "close"].includes(operation)) { revision.current++; setScreen(null); }
    try {
      const next = await request<ContentSessionStatus>(`content_session.${operation}`, {url: videoUrl, same_account: sameAccount});
      setStatus(next); setNetworkError(""); setViewError(""); setViewAttempt(value => value + 1);
    } catch (failure) {
      failed(failure, operation === "open", operation === "open" ? "open" : "status");
      if (failure instanceof YouTubeCapabilityError) {
        try {
          const next = await youtubeCapability<ContentSessionStatus>("content_session.status");
          setStatus(next);
          if (next.state === "waiting_resources") {setViewError(""); setError("");}
        }
        catch (transport) { failed(transport, false, "status"); }
      }
    } finally { setBusy(""); }
  };
  const input = (operation: string, args: Record<string, unknown>) => {
    if (!screen || viewPaused || changingBrowser) return;
    const observed = revision.current;
    // Failed or retired views discard pending input; never replay credentials on recovery.
    queued.current = queued.current.then(async () => {
      if (observed === revision.current) await request(`content_session.${operation}`, args);
    }).catch(failure => { if (observed === revision.current) failed(failure, true, "open"); });
  };
  return <section aria-label="YouTube 内容会话" className="space-y-3 mt-5">
    <h3 className="text-[15px] font-medium">YouTube 内容会话</h3>
    <p className="text-[13px] text-muted">内容登录用于获取字幕；Google 官方授权用于读取订阅，两项需要分别完成。关闭此页面不会断开后台连接。</p>
    <p className="text-[13px]">打开登录画面只会启动服务器浏览器，不会替你登录。</p>
    <p role="status">{networkError ? "暂时无法确认字幕能力" : ({disconnected: "内容会话已断开", waiting_resources: "等待服务器资源", available: "字幕能力已验证", unverified: "字幕能力尚未验证", needs_user: "需要完成 YouTube 登录", needs_maintenance: "内容连接需要维护", interrupted: "字幕能力中断", maintaining: "后台正在维护内容会话"})[status?.state || "unverified"]}</p>
    {(networkError || viewError || error || status?.reason) && <p role="alert" className="text-[13px] text-red-600">{networkError || viewError || error || status?.reason}</p>}
    {maintenance && maintenance.state !== "idle" && <p role="note" aria-label="自动会话维护" className="text-[13px]">
      {maintenance.reason}
      {maintenance.deadline_at && ` 最迟 ${time(maintenance.deadline_at)} 关闭。`}
      {maintenance.next_at && ` 下一次可执行：${time(maintenance.next_at)}。`}
      {` 滚动 24 小时已用 ${maintenance.used}/${maintenance.limit} 次。`}
    </p>}
    <p className="text-[12px] text-muted">{status?.account_consistency === "user_confirmed_not_verified" ? "账号一致性：用户确认，系统未自动核验。" : "账号一致性尚未确认。"}</p>
    <div className="flex flex-wrap gap-2">
      {viewPaused ? <button className={button} disabled={!!busy} onClick={() => void run(retryAction)}>{busy ? "正在重新连接…" : "重试连接"}</button>
        : <button className={button} disabled={!!busy} onClick={() => void run("open")}>{busy === "open" ? "正在打开登录画面…" : handoffBrowser ? "刷新登录画面" : "打开登录画面"}</button>}
      {(status?.browser_running || status?.browser_pending) && <button className={button} disabled={!!busy || (viewPaused && !status?.browser_pending)} onClick={() => void run("close")}>{busy === "close" ? "正在关闭…" : "关闭服务器浏览器"}</button>}
    </div>
    {busy === "open" && <p aria-live="polite" className="text-[13px]">正在连接服务器并打开浏览器，请稍候。登录画面会显示在这里，不会打开你电脑上的新窗口。</p>}
    {handoffBrowser && !viewPaused && <div className="space-y-2">
      <p className="text-[13px]">请在下方 YouTube 页面右上角点击“登录 / Sign in”，再在 Google 页面输入账号、密码和验证码。</p>
      <p className="text-[12px] text-muted">请核对远程浏览器地址栏，确认登录页面属于 youtube.com 或 accounts.google.com。Google 登录后返回 YouTube；若仍显示“登录 / Sign in”，请继续点击完成登录。</p>
      <p className="text-[12px] text-muted">点击画面后可输入或粘贴。闲置 15 分钟自动关闭，可重新打开。浏览器画面不会保存。</p>
      <button className={button} disabled={!screen || changingBrowser} onClick={() => input("youtube", {})}>返回 YouTube 页面</button>
      {!screen && !changingBrowser && <p aria-live="polite" className="text-[13px]">正在加载服务器登录画面…</p>}
      {screen && <div role="application" aria-label="服务器 Google 浏览器" tabIndex={0}
        className="outline-none focus:ring-2 focus:ring-accent"
        onKeyDown={event => {
          if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "v") return;
          event.preventDefault();
          if (event.key.length === 1 && !event.metaKey && !event.ctrlKey) input("text", {text: event.key});
          else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") input("key", {key: "ControlOrMeta+A"});
          else if (["Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) input("key", {key: event.key === "Tab" && event.shiftKey ? "Shift+Tab" : event.key});
        }}
        onPaste={event => { event.preventDefault(); input("text", {text: event.clipboardData.getData("text").slice(0, 4096)}); }}
        onWheel={event => input("scroll", {delta: event.deltaY})}>
        <img alt="服务器登录页面" src={`data:image/png;base64,${screen.image}`} draggable={false} className="w-full border border-line"
          onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); event.currentTarget.parentElement?.focus(); input("click", {x: (event.clientX - rect.left) * screen.width / rect.width, y: (event.clientY - rect.top) * screen.height / rect.height}); }} />
      </div>}
    </div>}
    <label className="flex gap-2 text-[13px]"><input type="checkbox" checked={sameAccount} onChange={e => setSameAccount(e.target.checked)} />我确认两条连接使用同一 Google 账号（用户确认，系统未自动核验）</label>
    <label className="block text-[13px]">用于检查字幕的视频链接<input aria-label="用于检查字幕的视频链接" value={videoUrl} maxLength={4096} onChange={e => setVideoUrl(e.target.value)} className="block w-full border border-line rounded p-2 bg-paper" placeholder="https://www.youtube.com/watch?v=…" /></label>
    <div className="flex flex-wrap gap-2">
      <button className={button} disabled={!!busy || viewPaused || !sameAccount || !handoffBrowser || !videoUrl.trim()} onClick={() => void run("confirm")}>{busy === "confirm" ? "正在检查字幕…" : "完成登录并检查字幕"}</button>
      <button className={button} disabled={!!busy || !!networkError || !status?.snapshot_present || !videoUrl.trim()} onClick={() => void run("verify")}>{busy === "verify" ? "正在获取字幕…" : "重新实际获取字幕"}</button>
    </div>
    <p className="text-[12px] text-muted">检查时会关闭服务器浏览器并保存当前会话，再实际获取此视频的字幕。需要继续登录时可重新打开；检查不会为此视频生成文稿。</p>
    {status?.last_success_at && <p className="text-[12px] text-muted">最近字幕成功：{new Date(status.last_success_at * 1000).toLocaleString()}</p>}
  </section>;
}
