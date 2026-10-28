import { useTranslation } from "react-i18next";
import { yt } from "./youtube/text";
import { useEffect, useState } from "react";
import { youtubeCapability as call } from "../api";
import { PanelHead } from "./IntegrationsView";
import { NoticeStack } from "./NoticeStack";

type TranslationStage = "initial" | "review" | "revision" | "composition";
type Stage = TranslationStage | "summary";
type Config = {
  version: number;
  country: string;
  prompts: Record<TranslationStage, Record<string, string>>;
  max_calls: number;
  max_tokens: number;
};
type Response = { settings: Config; defaults: Config; required: Record<TranslationStage, Record<string, string[]>>; summary_prompt: string; default_summary_prompt: string; legacy_settings?: string | null };
const input = "w-full rounded-lg border border-line bg-paper px-3 py-2 text-[13px] text-ink focus:border-accent outline-none";
const button = "rounded-lg border border-line px-3 py-2 text-[13px] hover:bg-panel disabled:opacity-40";

export function YouTubeTranslationSettings() {
  useTranslation();
  const stages: [Stage, string][] = [
    ["initial", yt("初译")], ["review", yt("审校")], ["revision", yt("修订")], ["composition", yt("成稿整理")], ["summary", yt("摘要")],
  ];
  const [data, setData] = useState<Response | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [summaryDraft, setSummaryDraft] = useState("");
  const [stage, setStage] = useState<Stage>("initial");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    void call<Response>("translation.settings").then((value) => {
      setData(value); setDraft(value.settings); setSummaryDraft(value.summary_prompt);
    }).catch((e) => setError(String(e.message || e)));
  };
  useEffect(load, []);
  const dirty = !!data && (JSON.stringify(draft) !== JSON.stringify(data.settings) || summaryDraft !== data.summary_prompt);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const save = async () => {
    if (!draft) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await call<Response>("translation.set_settings", { settings: draft, summary_prompt: summaryDraft });
      setData(value); setDraft(value.settings); setSummaryDraft(value.summary_prompt); setMessage(yt("已保存，后续新任务使用这些设置。"));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <section aria-label={yt("YouTube 生成规则")}>
    <PanelHead title={yt("YouTube 生成规则")} sub={yt("修改只影响后续新任务。")} />
    <NoticeStack messages={[error, message].map(value => value ? yt(value) : "")} />
    {!data || !draft ? <button className={button} onClick={load}>{error ? yt("重新加载") : yt("加载中…")}</button> : <>
      <div className="flex flex-wrap gap-2 mb-5" role="tablist" aria-label={yt("翻译阶段")}>
        {stages.map(([key, label]) => <button key={key} role="tab" aria-selected={stage === key}
          className={button + (stage === key ? " bg-panel text-accent" : " text-muted")}
          onClick={() => { setStage(key); }}>{label}</button>)}
      </div>
      <fieldset disabled={busy} className="space-y-5">
        {stage === "summary" ? <>
          <div>
            <label htmlFor="youtube-summary-prompt" className="block text-[13px] font-medium mb-2">{yt("摘要提示词")}</label>
            <textarea id="youtube-summary-prompt" className={input + " leading-relaxed resize-y"}
              rows={7} value={summaryDraft} maxLength={4000}
              onChange={(event) => { setMessage(""); setSummaryDraft(event.target.value); }} />
            <p className="mt-2 text-[12px] text-muted">{yt("摘要概括完整成稿，最多 120 个字符；已生成的摘要不会因修改提示词而重做。")}</p>
          </div>
          <button className={button} onClick={() => { setMessage(""); setSummaryDraft(data.default_summary_prompt); }}>{yt("恢复本阶段默认提示词")}</button>
        </> : <>
        <p className="text-[13px] text-muted">{yt("翻译使用 translation-agent 原始完整流程与提示词，成稿规则由 Edison 固定维护。旧自定义模板与单视频累计消费预算不再用于新任务。")}</p>
        <pre className="whitespace-pre-wrap break-words text-[12px] text-muted">{draft.prompts[stage].system}</pre>
        <label className="block text-[13px] font-medium">{yt("译文地区（可选）")}<input aria-label={yt("译文地区")} className={input + " mt-2"} value={draft.country} maxLength={100} onChange={(event) => setDraft({ ...draft, country: event.target.value })} /></label>
        </>}
      </fieldset>
      {data.legacy_settings && <details className="mt-5 text-[12px] text-muted">
        <summary>{yt("查看保留的旧版模板")}</summary>
        <p className="my-2">{yt("旧版使用不同输出协议，已完整保留供查阅；当前任务使用上方的上游模板。")}</p>
        <pre className="whitespace-pre-wrap break-words max-h-64 overflow-auto">{data.legacy_settings}</pre>
      </details>}
      <div className="flex items-center gap-3 mt-5">
        <button className="rounded-lg bg-accent text-white px-4 py-2 text-[13px] disabled:opacity-40" disabled={busy || !dirty} onClick={() => void save()}>{busy ? yt("保存中…") : yt("保存设置")}</button>
        <span role="status" className="text-[12px] text-muted">{dirty ? yt("有未保存的修改") : yt("已保存")}</span>
      </div>
    </>}
  </section>;
}
