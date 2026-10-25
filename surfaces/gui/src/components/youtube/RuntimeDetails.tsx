import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CapabilityHealth } from "./CapabilityHealth";
import { DiscoveryStatus } from "./DiscoveryStatus";
import type { Activity } from "./types";
import { yt } from "./text";

const at = (value?: number | null) => value ? new Date(value * 1000).toLocaleString() : yt("尚无记录");

/** Read the existing observation only. Opening diagnostics never starts a check or retry. */
export function RuntimeDetails({ activity, receivedAt, disconnected }: {
  activity: Activity | null; receivedAt: number; disconnected: boolean;
}) {
  useTranslation();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (!activity) return <p className="yp-runtime-empty" role="status">{yt("运行信息尚未收到，正在等待后台响应。")}</p>;
  const stale = disconnected || !receivedAt || now - receivedAt > 12000;
  const heartbeat = activity.runtime?.heartbeat_at;
  const live = !stale && activity.runtime?.worker_alive && !!heartbeat && now - heartbeat * 1000 < 10000;
  return <div className="yp-runtime-details">
    {stale && <p className="yp-runtime-stale" role="status">{yt("连接正在恢复，以下是最近收到的信息。")}</p>}
    <section className="yp-runtime-section" aria-label={yt("后台与模型")}>
      <h3>{yt("后台与模型")}</h3>
      <dl className="yp-runtime-facts">
        <div><dt>{yt("后台进程")}</dt><dd>{stale ? yt("暂未确认") : live ? yt("后台在线") : yt("后台心跳未确认")}</dd></div>
        <div><dt>{yt("最近响应")}</dt><dd>{at(heartbeat)}</dd></div>
        <div><dt>{yt("生成模型")}</dt><dd>{activity.model || yt("尚无记录")} · {activity.model_ready ? yt("已配置") : yt("尚未配置")}</dd></div>
      </dl>
      <p className="yp-runtime-caption">{yt("后台在线和模型已配置不代表任务已完成，处理结果以任务记录为准。")}</p>
    </section>
    <CapabilityHealth health={activity.health} />
    {activity.health?.notice.reason && <p className="yp-runtime-caption">{yt(activity.health.notice.reason)}</p>}
    {activity.health?.notice.since && <p className="yp-runtime-caption">{yt("开始于：{{time}}", {time: at(activity.health.notice.since)})}</p>}
    {!activity.health && <p className="yp-runtime-caption">{yt("连接检查尚无记录。")}</p>}
    <DiscoveryStatus discovery={activity.discovery} paused={activity.drain_paused} />
    <p className="yp-runtime-updated">{yt("数据更新于 {{time}}", {time: receivedAt ? new Date(receivedAt).toLocaleString() : yt("尚无记录")})}</p>
  </div>;
}
