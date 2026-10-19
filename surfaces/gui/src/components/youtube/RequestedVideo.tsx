import { useEffect, useRef, useState } from "react";
import { youtubeCapability as call } from "../../api";
import type { Asset } from "./types";
import { yt } from "./text";
import { Markdown } from "../Markdown";
import { activityLabels } from "./activityLabels";

export function RequestedVideo({ videoId, onDocument }: { videoId: string; onDocument: (asset: Asset) => void }) {
  const [asset, setAsset] = useState<Asset | null>(null);
  const [error, setError] = useState("");
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    let stopped = false;
    const read = async () => { try { const result = await call<Asset>("library.inspect", {video_id:videoId}); if (!stopped) {setAsset(result); setError("");} } catch { if (!stopped) setError(yt("暂时无法读取此视频的状态；已保存请求不会丢失。")); } };
    setAsset(null); void read(); ref.current?.focus();
    const timer = setInterval(read, 2000);
    return () => { stopped = true; clearInterval(timer); };
  }, [videoId]);
  return <section ref={ref} tabIndex={-1} className="yp-manual-focus" aria-label={yt("已接收的视频")}>
    <p>{yt("请求已保存；接受不等于已经开工。")}</p>
    <h2>{asset?.title || videoId}</h2>
    <p role="status">{error || (asset ? activityLabels()[asset.preparation_state] || yt(asset.preparation_state) : yt("正在读取处理状态…"))}</p>
    {asset?.failure_reason && <p>{yt(asset.failure_reason)}</p>}
    {asset?.manuscript_version && <button className="yp-btn" onClick={() => onDocument(asset)}>{yt("打开现有文档")}</button>}
  </section>;
}

export function RequestedDocument({ asset, onClose }: {asset: Asset; onClose: () => void}) {
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void call<{markdown: string}>("documents.get", {video_id:asset.video_id}).then(value => { if (alive) setMarkdown(value.markdown); }).catch(() => { if (alive) setError(yt("暂时无法读取现有文档，请重新打开。")); });
    return () => { alive = false; };
  }, [asset.video_id]);
  return <article className="yp-requested-document" aria-label={asset.title}>
    <button className="yp-btn" onClick={onClose}>{yt("返回文档库")}</button>
    <h1>{asset.title}</h1>
    {error ? <p role="alert">{error}</p> : <Markdown text={markdown} />}
  </article>;
}
