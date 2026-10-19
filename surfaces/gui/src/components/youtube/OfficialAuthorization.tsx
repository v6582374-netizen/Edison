import { useState } from "react";
import { youtubeCapability } from "../../api";
import type { Connection } from "./types";
import { yt } from "./text";

/** Trusted-device handoff stays in memory and never enters browser storage. */
export function OfficialAuthorization({ connection, onChange }: {
  connection: Connection; onChange: (value: Connection) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const capability = connection.subscription_capability;
  const when = (value?: number | null) => value ? new Date(value * 1000).toLocaleString() : yt("尚未验证");
  async function request(name: string, args = {}) {
    setBusy(true); setError("");
    try { onChange(await youtubeCapability<Connection>(name, args)); }
    catch (failure) {
      setError(failure instanceof Error ? failure.message : yt("连接检查失败"));
      try { onChange(await youtubeCapability<Connection>("connection.status")); } catch { /* retain last observed evidence */ }
    } finally { setBusy(false); }
  }
  return <section aria-label={yt("Google 官方授权")}>
    <h3>{yt("Google 官方授权")}</h3>
    <p role="status">{capability?.state === "available" ? yt("订阅能力已验证") : capability?.state === "interrupted" ? yt("订阅能力中断") : yt("订阅能力尚未验证")}</p>
    <p>{yt("最近成功")}: {when(capability?.last_success_at)}</p>
    {capability?.last_failure_at && <p>{yt("最近失败")}: {when(capability.last_failure_at)} · {capability.last_failure_reason || capability.reason}</p>}
    {capability?.reason && <p role="alert">{capability.reason}</p>}
    <button className="yp-btn" disabled={busy || !connection.authorized} onClick={() => void request("connection.refresh_subscriptions")}>{yt("检查订阅能力")}</button>
    <details>
      <summary>{yt("首次连接或重新授权")}</summary>
      <p>{yt("在可信设备运行 scripts/youtube_authorize.py 完成 Google 授权，再选择生成的交接文件。服务器会验证刷新与订阅读取；成功后删除交接文件。")}</p>
      <label>{yt("官方授权交接文件")}
        <input type="file" accept="application/json,.json" disabled={busy} onChange={async event => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (!file) return;
          if (file.size > 16384) { setError(yt("授权交接文件无效")); return; }
          let payload;
          try {
            payload = JSON.parse(await file.text());
            if (!payload || ![payload.client_id, payload.client_secret, payload.refresh_token].every(value => typeof value === "string" && value.length > 0)) throw new Error();
          } catch { setError(yt("授权交接文件无效")); return; }
          await request("connection.import_authorization", { client_id: payload.client_id, client_secret: payload.client_secret, refresh_token: payload.refresh_token });
        }} />
      </label>
    </details>
    {error && <p role="alert">{error}</p>}
    <p className="yp-inline-status">{yt("官方授权只验证订阅读取，不证明字幕能力。Google External + Testing 模式的 YouTube 授权通常在 7 天后失效；请核验 OAuth 应用发布配置。")}</p>
  </section>;
}
