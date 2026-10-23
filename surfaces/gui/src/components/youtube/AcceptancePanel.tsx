import { useTranslation } from "react-i18next";
import { yt } from "./text";
import { youtubeCapability as call } from "../../api";
import type { Acceptance, AcceptancePeriod } from "./types";

const labels: Record<Acceptance["state"], string> = {
  not_started: "尚未开始", observing: "验收中", passed: "通过", failed: "不通过", unverified: "尚未验证",
};
const reasons: Record<string, string> = {
  security_verification: "完成 Google 安全验证", first_connection: "首次连接", reconnect: "重新连接",
  manual_session_open: "手动打开服务器浏览器", manual_check: "手动检查字幕", credential_replacement: "替换官方授权凭据",
  credential_move: "人工搬运 Cookie", manual_retry: "点击重试", disconnect: "断开 YouTube",
  subscription_sync: "同步订阅", maintenance_window: "切换维护操作",
};
const at = (value: number) => new Date(value * 1000).toLocaleString();

function Period({ period }: { period: AcceptancePeriod }) {
  return <>
    <p>{yt("第 {{number}} 个连续周期 · {{reason}} · 开始于 {{time}}", { number: period.number, reason: yt(period.reason), time: at(period.started_at) })}</p>
    <p>{yt("计时：已满 {{days}} / {{required}} 天", { days: period.complete_days, required: period.required_days })}
      {period.missing_days.length > 0 && ` · ${yt("缺少每日记录：第 {{days}} 天", { days: period.missing_days.join("、") })}`}</p>
    <p>{yt("非主动 Google 安全验证：{{count}} / {{limit}} 次", { count: period.security_verifications.natural, limit: period.security_verifications.limit })}
      {period.security_verifications.controlled > 0 && ` · ${yt("受控演练 {{count}} 次另计", { count: period.security_verifications.controlled })}`}</p>
    <p>{yt("人工介入：{{total}} 次，其中普通恢复 {{ordinary}} 次", { total: period.interventions.total, ordinary: period.interventions.ordinary })}</p>
    {Object.entries(period.interventions.by_reason).length > 0 && <ul aria-label={yt("人工介入原因")}>
      {Object.entries(period.interventions.by_reason).map(([reason, count]) => <li key={reason}>{yt(reasons[reason] || reason)} × {count}</li>)}
    </ul>}
    <ul aria-label={yt("关键事件覆盖")}>
      {period.coverage.map(item => <li key={item.key}>{item.covered ? "✓" : yt("缺")} {yt(item.label)}</li>)}
    </ul>
    {period.failures.map(failure => <p key={failure} role="alert">{yt(failure)}</p>)}
    {period.controlled_events > 0 && <p>{yt("受控事件 {{count}} 条，单独记账，不计入自然事件覆盖。", { count: period.controlled_events })}</p>}
  </>;
}

export function AcceptancePanel({ acceptance, onChange, onError }: {
  acceptance?: Acceptance; onChange: () => Promise<void>; onError: (message: string) => void;
}) {
  useTranslation();
  if (!acceptance) return null;
  const act = (capability: string, args = {}) => void call(capability, args).then(onChange).catch(error => onError(String(error)));
  const exportEvidence = () => void call<Record<string, unknown>>("acceptance.export").then(evidence => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2)], { type: "application/json" }));
    link.download = `edison-youtube-acceptance-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }).catch(error => onError(String(error)));
  const active = acceptance.current && acceptance.current.ended_at === null;
  return <section className="yp-acceptance" aria-label={yt("持续验收")}>
    <strong role="status">{yt("持续验收：{{state}}", { state: yt(labels[acceptance.state]) })}</strong>
    {acceptance.reason && <p>{yt(acceptance.reason)}</p>}
    {acceptance.controlled_boundary && <p>{yt("本实例的外部服务为受控替身，全部证据按受控记账，不能证明自然事件。")}</p>}
    {acceptance.drill && <p>{yt("故障演练进行中：期间事件单独记账。")}</p>}
    {acceptance.current ? <Period period={acceptance.current} /> : <p>{yt("正式路径检查通过后开始；日历天数本身不会判定通过。")}</p>}
    {acceptance.history.length > 0 && <details>
      <summary>{yt("此前周期 {{count}} 个", { count: acceptance.history.length })}</summary>
      <ul aria-label={yt("此前周期")}>{acceptance.history.map(period => <li key={period.number}>
        {yt("第 {{number}} 个：{{state}}", { number: period.number, state: yt(labels[period.state]) })}
        {period.failures.map(failure => <p key={failure}>{yt(failure)}</p>)}
      </li>)}</ul>
    </details>}
    {!active && <button className="yp-btn" onClick={() => act("acceptance.begin")}>{yt("开始持续验收")}</button>}
    <button className="yp-btn" onClick={() => act("acceptance.drill", { active: !acceptance.drill })}>
      {acceptance.drill ? yt("结束故障演练") : yt("开始故障演练")}</button>
    <button className="yp-btn" onClick={exportEvidence}>{yt("导出验收证据")}</button>
  </section>;
}
