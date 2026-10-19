import { useState } from "react";
import { youtubeCapability as call, YouTubeCapabilityError } from "../../api";
import type { Asset } from "./types";
import { yt } from "./text";
import "./addVideo.css";

export type VideoRequest = { accepted: boolean; disposition: "progress" | "document" | "action" | "unconfirmed" | "deleted"; asset?: Asset };
const pendingKey = "edison:youtube:pending-submission";
function pending(): {url: string; requestId: string} | null {
  try { const value = JSON.parse(sessionStorage.getItem(pendingKey) || "null"); return value && typeof value.url === "string" && typeof value.requestId === "string" ? value : null; } catch { return null; }
}
export function AddVideo({ onAccepted }: { onAccepted: (result: VideoRequest) => void }) {
  const [saved] = useState(pending);
  const [url, setUrl] = useState(saved?.url || "");
  const [requestId, setRequestId] = useState(saved?.requestId || "");
  const [uncertain, setUncertain] = useState(!!saved);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<VideoRequest | null>(null);
  function remember(id: string) {
    try { sessionStorage.setItem(pendingKey, JSON.stringify({url, requestId: id})); } catch { /* memory still retains this request */ }
  }
  function received(value: VideoRequest) {
    if (!value.accepted) { setUncertain(true); setMessage(yt("结果待确认；尚未查到保存记录，可再次核对或幂等重发。")); return; }
    try { sessionStorage.removeItem(pendingKey); } catch { /* optional persistence */ }
    setUncertain(false); setResult(value); setMessage("");
    if (value.disposition === "document" || value.disposition === "progress") onAccepted(value);
  }
  async function confirm(id = requestId) {
    setBusy(true);
    try { received(await call<VideoRequest>("manual.confirm", {request_id: id})); }
    catch { setUncertain(true); setMessage(yt("结果待确认；连接恢复后请核对保存记录，不会自动添加。")); }
    finally { setBusy(false); }
  }
  async function submit(action?: "restore" | "retry") {
    const id = action ? crypto.randomUUID() : requestId || crypto.randomUUID();
    setRequestId(id); remember(id); setBusy(true); setMessage("");
    try { received(await call<VideoRequest>(action ? `manual.${action}` : "manual.submit", action ? {video_id: result?.asset?.video_id, request_id:id} : {url, request_id:id})); }
    catch (error) {
      if (error instanceof YouTubeCapabilityError) {
        setUncertain(false); setMessage((action ? yt("操作未完成。") : yt("尚未添加。")) + " " + yt(error.message));
        try { sessionStorage.removeItem(pendingKey); } catch { /* optional persistence */ }
      } else { setUncertain(true); setMessage(yt("结果待确认；正在核对保存记录。")); await confirm(id); }
    } finally { setBusy(false); }
  }
  return <section className="yp-add-video" aria-labelledby="add-video-heading">
    <h1 id="add-video-heading">{yt("添加视频")}</h1>
    <p>{yt("粘贴一个 YouTube 视频链接，将它加入现有处理流程。")}</p>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label htmlFor="youtube-video-url">{yt("视频链接")}</label>
      <input id="youtube-video-url" type="url" required autoComplete="off" placeholder="https://www.youtube.com/watch?v=…" value={url} disabled={busy}
        aria-describedby="add-video-help add-video-status" onChange={event => {
          setUrl(event.target.value); setRequestId(""); setResult(null); setUncertain(false); setMessage("");
          try { sessionStorage.removeItem(pendingKey); } catch { /* optional persistence */ }
        }} />
      <button className="yp-btn primary" disabled={busy || !url.trim()}>{busy ? yt("正在检查并保存…") : uncertain ? yt("幂等重发") : yt("添加并处理")}</button>
    </form>
    <p id="add-video-help" className="yp-inline-status">{yt("无需开启自动更新。时间参数不会截取视频；播放列表链接只处理其中明确指定的视频。")}</p>
    <p id="add-video-status" role="status" aria-live="polite">{message}</p>
    {uncertain && <button className="yp-btn" disabled={busy} onClick={() => void confirm()}>{yt("核对保存记录")}</button>}
    {result?.asset && result.disposition === "action" && <div className="yp-add-existing">
      <h2>{result.asset.title}</h2><p>{yt(result.asset.failure_reason || "已有任务尚未完成；重新提交链接不会重置其状态。")}</p>
      {result.asset.preparation_state === "cancelled" ? <button className="yp-btn" disabled={busy} onClick={() => void submit("restore")}>{yt("恢复此视频处理")}</button>
        : ["failed", "unavailable"].includes(result.asset.preparation_state) ? <button className="yp-btn" disabled={busy} onClick={() => void submit("retry")}>{yt("重试此视频")}</button> : <p>{yt("此视频是 Shorts，不能加入处理。")}</p>}
    </div>}
    {result?.disposition === "deleted" && <p>{yt("此前请求已保存，但视频后来被删除；如需再次添加，请重新提交链接。")}</p>}
  </section>;
}
