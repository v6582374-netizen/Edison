import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";
import { TaskQueue } from "./focus/TaskQueue";
import { useFocusQueue } from "./focus/useFocusQueue";
import "./focus/focus.css";

export function FocusView() {
  const { t } = useTranslation();
  const queue = useFocusQueue();
  const [draft, setDraft] = useState("");
  const [panel, setPanel] = useState<"capture" | "queue" | null>(null);
  const captureButton = useRef<HTMLButtonElement>(null);
  const queueButton = useRef<HTMLButtonElement>(null);
  const captureInput = useRef<HTMLTextAreaElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const panelElement = useRef<HTMLElement>(null);
  const toolsElement = useRef<HTMLDivElement>(null);
  const text = queue.current?.text || t("focus.empty_title");
  // Size the words, not a container: longer notes retain a comfortable reading measure.
  const length = Array.from(text).length;
  const measure = length > 70 ? "long" : length > 24 ? "medium" : "short";
  const cjk = /[\u3400-\u9fff]/u.test(text);

  useLayoutEffect(() => {
    if (panel === "capture") captureInput.current?.focus();
    if (panel === "queue") closeButton.current?.focus();
  }, [panel]);
  useEffect(() => {
    if (!panel) return;
    function dismissOutside(event: PointerEvent) {
      if (event.button !== 0 || !(event.target instanceof Node)) return;
      if (panelElement.current?.contains(event.target) || toolsElement.current?.contains(event.target)) return;
      // Leave focus with the clicked element; explicit dismissal restores its trigger.
      setPanel(null);
    }
    document.addEventListener("pointerdown", dismissOutside);
    return () => document.removeEventListener("pointerdown", dismissOutside);
  }, [panel]);
  function closePanel() {
    const trigger = panel === "queue" ? queueButton : captureButton;
    setPanel(null);
    trigger.current?.focus();
  }
  function togglePanel(next: "capture" | "queue") {
    if (panel === next) closePanel(); else setPanel(next);
  }
  function capture() {
    if (!draft.trim()) return;
    queue.add(draft);
    setDraft("");
    closePanel();
  }

  return <main className="focus-module" aria-label="minimalism" onKeyDown={e => {
    if (e.key === "Escape" && panel && !e.defaultPrevented) { e.preventDefault(); closePanel(); }
  }}>
    <header className="fm-header">
      <h1 className="editorial-heading">minimalism</h1>
      <div ref={toolsElement} className="fm-tools">
        <button ref={captureButton} aria-expanded={panel === "capture"} aria-controls="focus-tools-panel" onClick={() => togglePanel("capture")}>{t("focus.capture")}</button>
        <button ref={queueButton} aria-expanded={panel === "queue"} aria-controls="focus-tools-panel" onClick={() => togglePanel("queue")}>{t("focus.arrange")}</button>
      </div>
    </header>
    {queue.saveError && <p className="fm-save-error" role="alert">{t("focus.save_error")}</p>}
    <section className="fm-stage" aria-label={t("focus.current_task")}>
      <div className="fm-words">
        <h2 className="fm-task" data-measure={measure} data-script={cjk ? "cjk" : "latin"} aria-live="polite" aria-atomic="true">{text}</h2>
        {queue.current && <button className="fm-complete" onClick={queue.complete}>{t("focus.complete")}</button>}
      </div>
    </section>
    {panel && <aside ref={panelElement} id="focus-tools-panel" className="fm-panel" aria-label={panel === "capture" ? t("focus.capture") : t("focus.planning")}>
      <div className="fm-panel-header">
        <h2>{panel === "capture" ? t("focus.capture") : t("focus.arrange")}</h2>
        <button ref={closeButton} onClick={closePanel} aria-label={t("focus.close_panel")}><Icon name="x" size={16} /></button>
      </div>
      {panel === "capture" ? <form className="fm-capture-form" onSubmit={e => { e.preventDefault(); capture(); }}>
        <label htmlFor="focus-capture">{t("focus.capture_label")}</label>
        <textarea id="focus-capture" ref={captureInput} rows={3} maxLength={500} placeholder={t("focus.capture_placeholder")} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); capture(); }
        }} />
        <div className="fm-capture-actions"><span>{t("focus.enter_hint")}</span><button type="submit" disabled={!draft.trim()}>{t("focus.add")}</button></div>
      </form> : <TaskQueue queue={queue} />}
    </aside>}
  </main>;
}
