import { useTranslation } from "react-i18next";
import { yt } from "./text";
import type { Capability, Health } from "./types";
import { Icon } from "../Icon";

const at = (value?: number | null) => value ? new Date(value * 1000).toLocaleString() : yt("尚无记录");
const capabilityName = (name?: string) => name === "subscriptions" ? yt("订阅能力") : yt("字幕能力");
const categories: Record<string, string> = {
  network: "网络暂时中断", rate_limited: "YouTube 请求限流", rate_limit: "YouTube 请求限流",
  service: "服务暂不可用", quota: "官方 API 项目额度已用尽", budget: "本机 API 日预算已用尽",
  session: "内容会话等待自动维护", resource: "等待服务器资源", timeout: "自动维护超时", unknown: "原因待核实",
  user: "需要你完成 Google 登录或安全验证", reauthorization: "需要重新授权 Google", authorization: "需要重新授权 Google",
  maintenance: "需要维护", storage: "需要维护：凭据存储不可用", configuration: "需要维护：OAuth 配置不可用",
  permission: "需要维护：授权权限不足",
};
export const categoryLabel = (value?: string | null) => value ? yt(categories[value] || "原因待核实") : "";
const states: Record<string, string> = {
  available: "可用", waiting: "等待自动恢复", attention: "需要处理", unverified: "尚未验证",
  disconnected: "已断开", interrupted: "已中断",
};
const guidance = (category?: string) =>
  category === "user" ? yt("后台已停止相关自动尝试。请在 YouTube 连接中打开服务器浏览器，亲自在 Google 页面完成验证。")
  : category === "reauthorization" || category === "authorization"
    ? yt("Google 拒绝了现有授权，原因未经证实为安全验证。请在 YouTube 连接中重新授权。")
    : yt("这不是重新登录能解决的问题，请检查服务器存储、权限或配置。");

/** One persistent in-site notice, never a repeated popup. */
export function CapabilityNotice({ health, onConnection, onDetails }: {
  health?: Health; onConnection?: () => void; onDetails?: () => void;
}) {
  useTranslation();
  const notice = health?.notice;
  if (!notice || notice.level === "none") return null;
  const name = notice.capability === "subscriptions" ? yt("订阅更新") : yt("字幕获取");
  const next = notice.next_check_at ? yt("下一次检查：{{time}}", { time: at(notice.next_check_at) }) : "";
  const connectionNeeded = notice.level === "attention" && ["user", "reauthorization", "authorization"].includes(notice.category || "");
  if (notice.level === "waiting")
    return <section className="yp-capability-wait" role="status" aria-label={yt("YouTube 等待说明")}>
      <Icon name="clock" />
      <div><strong>{yt("{{name}}暂缓", {name})}</strong>
        <p>{categoryLabel(notice.category)}{next ? ` · ${next}` : ""}</p>
        <p>{yt("已有文稿可以继续阅读，后台会按计划检查恢复。")}</p>
      </div>
      {onDetails && <button className="yp-status-action" onClick={onDetails}>{yt("查看运行详情")}</button>}
    </section>;
  return <section className={`yp-capability-alert ${notice.level}`} role="alert" aria-label={yt("YouTube 能力提示")}>
    <Icon name="warning" />
    <div><strong>{notice.level === "escalated"
      ? yt("{{name}}已中断超过 24 小时", { name })
      : `${name} · ${categoryLabel(notice.category)}`}</strong>
    <p>{notice.level === "escalated" ? yt("{{category}}。后台仍按计划低频检查，无需点击重试；已有资料可以继续阅读。", { category: categoryLabel(notice.category) }) : guidance(notice.category)}</p>
    {next && <p>{next}</p>}
    </div>
    {connectionNeeded && onConnection ? <button className="yp-status-action" onClick={onConnection}>{yt("查看连接")}</button>
      : onDetails && <button className="yp-status-action" onClick={onDetails}>{yt("查看运行详情")}</button>}
  </section>;
}

function Row({ name, item }: { name: string; item: Capability }) {
  return <div className="yp-capability-row">
    <dt>{capabilityName(name)}</dt>
    <dd>
      <strong>{yt(states[item.state] || item.state)}</strong>
      {item.fault && <span> · {categoryLabel(item.fault)}</span>}
      <span>{yt("最近成功：{{time}}", { time: at(item.last_success_at) })}</span>
      <span>{yt("最近失败：{{time}}", { time: at(item.last_failure_at) })}</span>
      {item.next_check_at && <span>{yt("下一次检查：{{time}}", { time: at(item.next_check_at) })}</span>}
      {item.reason && <span className="yp-runtime-reason">{yt(item.reason)}</span>}
    </dd>
  </div>;
}

/** Capability evidence beside, never inferred from, the thread heartbeat. */
export function CapabilityHealth({ health }: { health?: Health }) {
  useTranslation();
  if (!health) return null;
  const maintenance = health.maintenance;
  return <section className="yp-capability" aria-label={yt("能力状态")}>
    <h3>{yt("连接检查")}</h3>
    <dl>
      <Row name="subscriptions" item={health.capabilities.subscriptions} />
      <Row name="captions" item={health.capabilities.captions} />
      <div className="yp-capability-row">
        <dt>{yt("会话维护")}</dt>
        <dd>
          {maintenance.reason ? <span>{yt(maintenance.reason)}</span> : <span>{yt("当前无维护")}</span>}
          <span>{maintenance.last_started_at
            ? yt("最近一次：{{start}} 开始，{{end}}", { start: at(maintenance.last_started_at),
              end: maintenance.last_ended_at ? yt("{{time}} 结束", { time: at(maintenance.last_ended_at) }) : yt("尚未结束") })
            : yt("最近一次：尚无记录")}</span>
          {maintenance.last_result && <span>{yt(maintenance.last_result)}</span>}
        </dd>
      </div>
      <div className="yp-capability-row">
        <dt>{yt("真实任务进展")}</dt>
        <dd>
          <span>{yt("最近阶段变化：{{time}}", { time: at(health.progress.last_stage_at) })}</span>
          <span>{yt("最近成稿：{{time}}", { time: at(health.progress.last_ready_at) })}</span>
        </dd>
      </div>
    </dl>
    <p className="yp-progress-note">{yt("后台心跳只说明服务在线，不代表订阅、字幕能力可用或任务成功。")}</p>
  </section>;
}
