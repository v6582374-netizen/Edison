import { useTranslation } from "react-i18next";
import { mt } from "./text";
import { useEffect, useState } from "react";
import { minimalismCapability } from "../../api";
import { type ImageSettings, button, field, message, primary } from "./types";
export function ImageGenerationSettings() {
  useTranslation();
  const [config, setConfig] = useState<ImageSettings | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const load = () =>
    minimalismCapability<ImageSettings>("image.settings")
      .then(setConfig)
      .catch((e) => setError(message(e)));
  useEffect(() => {
    void load();
  }, []);
  async function save(clear_key = false) {
    if (!config) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      setConfig(
        await minimalismCapability<ImageSettings>("image.settings.save", {
          base_url: config.base_url,
          model: config.model,
          ...(key ? { api_key: key } : {}),
          clear_key,
        }),
      );
      setKey("");
      setSaved(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label={mt("图像生成设置")}>
      <h2 className="text-xl font-semibold text-ink">{mt("图像生成")}</h2>
      <p className="mt-2 mb-6 text-[13px] text-muted">
        {mt("用于 items 的 AI 封面，独立于聊天模型。")}
      </p>
      {error && (
        <div role="alert" className="mb-4 text-[13px] text-red-500">
          {mt(error)}{" "}
          {!config && (
            <button onClick={load} className={button}>
              {mt("重试")}
            </button>
          )}
        </div>
      )}
      {config ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          className="rounded-xl border border-line bg-panel p-5 space-y-5"
        >
          <label className="block text-[13px] text-ink">
            API Base URL
            <input
              required
              className={`${field} mt-2`}
              placeholder="https://your-provider.example/v1"
              value={config.base_url}
              onChange={(e) => {
                setSaved(false);
                setConfig({ ...config, base_url: e.target.value });
              }}
            />
          </label>
          <label className="block text-[13px] text-ink">
            {mt("图像模型")}
            <input
              required
              className={`${field} mt-2`}
              placeholder={mt("供应商支持的图像模型")}
              value={config.model}
              onChange={(e) => {
                setSaved(false);
                setConfig({ ...config, model: e.target.value });
              }}
            />
          </label>
          <label className="block text-[13px] text-ink">
            API Key
            <input
              type="password"
              autoComplete="new-password"
              className={`${field} mt-2`}
              placeholder={
                config.has_key ? mt("已保存，留空保留") : mt("输入 API Key")
              }
              value={key}
              onChange={(e) => {
                setSaved(false);
                setKey(e.target.value);
              }}
            />
          </label>
          <p className="text-[12px] text-muted">
            {mt(
              "支持 OpenAI 兼容的 images/generations 与 images/edits 接口。凭据由 Edison 保管，不随物品备份导出。",
            )}
          </p>
          <div className="flex items-center gap-3">
            <button className={primary} disabled={busy}>
              {busy ? mt("保存中…") : mt("保存")}
            </button>
            {config.has_key && (
              <button
                type="button"
                className={button}
                disabled={busy}
                onClick={() => void save(true)}
              >
                {mt("移除 API Key")}
              </button>
            )}
            {saved && (
              <span role="status" className="text-[13px] text-muted">
                {mt("已保存")}
              </span>
            )}
          </div>
        </form>
      ) : (
        !error && (
          <p role="status" className="text-muted">
            {mt("正在加载…")}
          </p>
        )
      )}
    </section>
  );
}
