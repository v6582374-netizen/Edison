import { useEffect, useRef, useState } from "react";
import { youtubeCapability as call } from "../api";

export type ContentSessionStatus = {
  state: "unverified" | "available" | "needs_user" | "needs_maintenance" | "interrupted";
  browser_running: boolean; snapshot_present: boolean; reason?: string;
  account_consistency?: string; last_success_at?: number; idle_closes_at?: number;
};
type Screen = { image: string; width: number; height: number; origin: string };
const button = "rounded-lg border border-line px-3 py-2 text-[13px] hover:bg-panel disabled:opacity-40";

export function YouTubeSessionSettings() {
  const [status, setStatus] = useState<ContentSessionStatus | null>(null);
  const [screen, setScreen] = useState<Screen | null>(null);
  const [sameAccount, setSameAccount] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queued = useRef(Promise.resolve());
  const refresh = () => call<ContentSessionStatus>("content_session.status").then(setStatus);
  useEffect(() => { void refresh().catch(e => setError(String(e.message || e))); }, []);
  useEffect(() => {
    if (!status?.browser_running) { setScreen(null); return; }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const [next, state] = await Promise.all([call<Screen>("content_session.screen"), call<ContentSessionStatus>("content_session.status")]);
        if (active) { setScreen(next); setStatus(state); }
      } catch (e) { if (active) { setError(String((e as Error).message)); void refresh().catch(() => {}); } }
      if (active) timer = setTimeout(poll, 1200);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); setScreen(null); };
  }, [status?.browser_running]);
  const run = async (operation: string) => {
    setBusy(true); setError("");
    try { setStatus(await call<ContentSessionStatus>(`content_session.${operation}`, {url: videoUrl, same_account: sameAccount})); }
    catch (e) { setError(String((e as Error).message)); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  };
  const input = (operation: string, args: Record<string, unknown>) => {
    // Ordered keystrokes go straight to the private Google browser; no local storage.
    queued.current = queued.current.then(async () => { await call(`content_session.${operation}`, args); }).catch(e => setError(String(e.message)));
  };
  return <section aria-label="YouTube 内容会话" className="space-y-3 mt-5">
    <h3 className="text-[15px] font-medium">YouTube 内容会话</h3>
    <p className="text-[13px] text-muted">在服务器专用浏览器登录 Google，再进入 YouTube 完成站点登录。关闭此页面不会断开后台连接。</p>
    <p role="status">{({available: "字幕能力已验证", unverified: "字幕能力尚未验证", needs_user: "需要完成 YouTube 登录", needs_maintenance: "内容连接需要维护", interrupted: "字幕能力中断"})[status?.state || "unverified"]}</p>
    {(error || status?.reason) && <p role="alert" className="text-[13px] text-red-600">{error || status?.reason}</p>}
    <p className="text-[12px] text-muted">{status?.account_consistency === "user_confirmed_not_verified" ? "账号一致性：用户确认，系统未自动核验。" : "账号一致性尚未确认。"}</p>
    <div className="flex flex-wrap gap-2">
      <button className={button} disabled={busy} onClick={() => void run("open")}>{status?.browser_running ? "继续服务器登录" : "打开服务器登录"}</button>
      {status?.browser_running && <button className={button} disabled={busy} onClick={() => void run("close")}>关闭服务器浏览器</button>}
    </div>
    {status?.browser_running && <div className="space-y-2">
      <p className="text-[12px] text-muted">下方是服务器浏览器画面。点击画面后直接输入；密码和验证码由你在 Google 页面完成。闲置 15 分钟自动关闭，可重新打开。浏览器画面不会保存。</p>
      <p className="text-[12px]">当前站点：{screen?.origin || "加载中"}</p>
      <button className={button} onClick={() => input("youtube", {})}>进入 YouTube 完成站点交接</button>
      {screen && <div role="application" aria-label="服务器 Google 浏览器" tabIndex={0}
        className="outline-none focus:ring-2 focus:ring-accent"
        onKeyDown={event => {
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
      <button className={button} disabled={busy || !sameAccount || !status?.browser_running || !videoUrl.trim()} onClick={() => void run("confirm")}>完成登录并检查字幕</button>
      <button className={button} disabled={busy || !status?.snapshot_present || !videoUrl.trim()} onClick={() => void run("verify")}>重新实际获取字幕</button>
    </div>
    <p className="text-[12px] text-muted">系统会实际获取此视频的字幕，确认连接可用；不会为检查视频生成文稿。</p>
    {status?.last_success_at && <p className="text-[12px] text-muted">最近字幕成功：{new Date(status.last_success_at * 1000).toLocaleString()}</p>}
  </section>;
}
