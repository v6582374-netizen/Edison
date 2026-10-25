import {useEffect, useRef, useState} from "react";
import {useTranslation} from "react-i18next";
import {Overlayer} from "foliate-js/overlayer.js";
import {getReaderNotebook, saveReaderNotebook, downloadReaderNotes, ReaderRequestError, type ReaderBook, type ReaderHighlight, type ReaderNotebook, type ReaderNotebookUpdate, type ReaderPosition} from "../../api";
import type {FoliateView} from "./engine";
import {captureHighlight, verifyPosition} from "./position";

const colors = {yellow: "#f4cf52", green: "#75cda3", pink: "#ed9eb9"};
export default function Notebook({book, view, open, target, onTargetHandled, onOpen, onGuard, onNavigate}: {
  book: ReaderBook; view: FoliateView | null; open: boolean; target: string | null; onTargetHandled: () => void; onOpen: (open: boolean) => void;
  onGuard: (guard: (() => boolean) | null) => void; onNavigate: (cfi: string) => Promise<void>;
}) {
  const {t} = useTranslation();
  const [record, setRecord] = useState<ReaderNotebook | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState<ReaderPosition | null>(null);
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [hint, setHint] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportFailed, setExportFailed] = useState(false);
  const consumedTarget = useRef<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const pending = useRef<ReaderNotebookUpdate | null>(null);
  const pendingActive = useRef<string | null>(null);
  const alive = useRef(true);
  const running = useRef(false);
  const callbacks = useRef({onOpen, onGuard, onNavigate, target, onTargetHandled}); callbacks.current = {onOpen, onGuard, onNavigate, target, onTargetHandled};
  const activeItem = record?.highlights.find(item => item.id === active);
  const original = active ? activeItem?.note ?? "" : record?.book_note ?? "";
  const dirty = draft !== original;
  const blocked = useRef(false); blocked.current = dirty || busy || !!pending.current;
  const editable = !!record && !busy && !error;
  const canSwitch = () => {
    if (!blocked.current) return true;
    setHint(t("reader.notebook_unsaved")); callbacks.current.onOpen(true); return false;
  };
  const switchTo = (id: string | null) => {
    if (!canSwitch()) return false;
    setActive(id); setDraft(id ? record?.highlights.find(item => item.id === id)?.note ?? "" : record?.book_note ?? "");
    setHint(""); setDeleting(null); callbacks.current.onOpen(true); return true;
  };
  const switchRef = useRef(switchTo); switchRef.current = switchTo;
  useEffect(() => {
    alive.current = true;
    callbacks.current.onGuard(() => {
      if (!blocked.current) return true;
      setHint(t("reader.notebook_unsaved")); callbacks.current.onOpen(true); return false;
    });
    void load();
    return () => {alive.current = false; callbacks.current.onGuard(null);};
  }, [book.id]);

  async function load() {
    if (running.current) return;
    running.current = true; setBusy(true);
    try {
      const next = await getReaderNotebook(book.id);
      if (next.content_version !== book.content_version) throw new Error(t("reader.version_error"));
      if (!alive.current) return;
      pending.current = null; setRecord(next); setActive(null); setDraft(next.book_note); setError(null); setHint("");
    } catch (error) {if (alive.current) {setError(error); callbacks.current.onOpen(true);}}
    finally {running.current = false; if (alive.current) setBusy(false);}
  }
  async function persist(next?: ReaderNotebook, nextActive = active) {
    if (running.current || !record) return;
    if (next) {pending.current = {...next, revision: record.revision, operation_id: crypto.randomUUID()}; pendingActive.current = nextActive;}
    nextActive = pendingActive.current;
    const operation = pending.current;
    if (!operation) return;
    running.current = true; setBusy(true); setError(null); setHint("");
    try {
      const saved = await saveReaderNotebook(book.id, operation);
      if (!alive.current) return;
      pending.current = null; setRecord(saved); setActive(nextActive);
      setDraft(nextActive ? saved.highlights.find(item => item.id === nextActive)?.note ?? "" : saved.book_note);
      for (const content of view?.renderer?.getContents() ?? []) content.doc.getSelection()?.removeAllRanges();
      setSelected(null); setDeleting(null);
    } catch (error) {if (alive.current) {setError(error); callbacks.current.onOpen(true);}}
    finally {running.current = false; if (alive.current) setBusy(false);}
  }

  useEffect(() => {
    if (!view || !record) return;
    let live = true;
    const cleanups: (() => void)[] = [];
    const attached = new Set<Document>();
    const attach = (doc: Document, index: number) => {
      if (attached.has(doc)) return;
      attached.add(doc);
      const selection = () => {
        const sel = doc.getSelection();
        if (!sel || sel.isCollapsed || !sel.rangeCount) {setSelected(null); return;}
        try {setSelected(captureHighlight(view, index, sel.getRangeAt(0))); setHint("");}
        catch {setSelected(null); setHint(t("reader.selection_limit"));}
      };
      doc.addEventListener("selectionchange", selection);
      cleanups.push(() => doc.removeEventListener("selectionchange", selection));
    };
    const draw = (event: Event) => {
      const {annotation, doc, draw} = (event as CustomEvent).detail;
      const item = record.highlights.find(item => item.anchor.cfi === annotation.value);
      if (!item) return;
      try {
        verifyPosition(view, item.anchor, doc);
        draw(Overlayer.highlight, {color: colors[item.color]});
      } catch {setInvalid(value => ({...value, [item.id]: true}));}
    };
    const redraw = async () => {
      await Promise.resolve(); // create-overlay is emitted before the engine installs it.
      if (!live) return;
      for (const content of view.renderer?.getContents() ?? []) attach(content.doc, content.index);
      for (const item of record.highlights) {
        if (!live) return;
        try {await view.addAnnotation({value: item.anchor.cfi});}
        catch {if (live) setInvalid(value => ({...value, [item.id]: true}));}
      }
    };
    const overlay = () => {void redraw();};
    const show = (event: Event) => {
      const {value, range} = (event as CustomEvent).detail;
      if (!range.startContainer.ownerDocument.getSelection()?.isCollapsed) return;
      const item = record.highlights.find(item => item.anchor.cfi === value);
      if (item) switchRef.current(item.id);
    };
    view.addEventListener("draw-annotation", draw);
    view.addEventListener("create-overlay", overlay);
    view.addEventListener("show-annotation", show);
    void redraw();
    void (async () => {
      const failures: Record<string, boolean> = {};
      const documents = new Map<number, Document>();
      for (const item of record.highlights) {
        if (!live) return;
        try {
          if (!documents.has(item.anchor.index)) documents.set(item.anchor.index, await view.book!.sections[item.anchor.index].createDocument());
          verifyPosition(view, item.anchor, documents.get(item.anchor.index)!);
        } catch {failures[item.id] = true;}
      }
      if (live) setInvalid(failures);
    })();
    return () => {
      live = false; cleanups.forEach(clean => clean());
      view.removeEventListener("draw-annotation", draw); view.removeEventListener("create-overlay", overlay); view.removeEventListener("show-annotation", show);
      // Also removes deleted/changed ranges; the next effect paints only current records.
      for (const item of record.highlights) void view.deleteAnnotation({value: item.anchor.cfi}).catch(() => {});
    };
  }, [view, record, t]);

  async function add() {
    if (!record || !selected || !editable || !canSwitch()) return;
    const existing = record.highlights.find(item => item.anchor.cfi === selected.cfi);
    if (existing) {switchTo(existing.id); setSelected(null); return;}
    const item: ReaderHighlight = {id: crypto.randomUUID(), anchor: selected, note: "", color: "yellow"};
    callbacks.current.onOpen(true);
    // Keep the intended focus across a lost-response retry.
    setActive(item.id); setDraft("");
    await persist({...record, highlights: [...record.highlights, item]}, item.id);
  }
  async function jump(item: ReaderHighlight) {
    if (!view || !switchTo(item.id)) return;
    try {
      verifyPosition(view, item.anchor, await view.book!.sections[item.anchor.index].createDocument());
      await callbacks.current.onNavigate(item.anchor.cfi);
      if (window.matchMedia("(max-width: 1100px)").matches) callbacks.current.onOpen(false);
    } catch {setInvalid(value => ({...value, [item.id]: true}));}
  }
  useEffect(() => {
    if (!target || consumedTarget.current === target || !view || !record || busy) return;
    consumedTarget.current = target;
    if (target === "book") {switchTo(null); callbacks.current.onTargetHandled(); return;}
    const item = record.highlights.find(item => item.id === target);
    if (item) void jump(item).finally(() => {
      if (alive.current && callbacks.current.target === target) callbacks.current.onTargetHandled();
    });
    else {callbacks.current.onOpen(true); setHint(t("reader.missing_highlight")); callbacks.current.onTargetHandled();}
  }, [target, view, record, busy]);
  async function exportNotes(format: "json" | "markdown") {
    if (!canSwitch() || exporting) return;
    setExporting(true); setExportFailed(false);
    try {await downloadReaderNotes(book, format);}
    catch {setExportFailed(true);}
    finally {if (alive.current) setExporting(false);}
  }
  const conflict = error instanceof ReaderRequestError && error.status === 409;
  const auth = error instanceof ReaderRequestError && [401,403].includes(error.status);
  return <>
    {selected && <div className="reader-selection" role="toolbar" aria-label={t("reader.selection")}>
      <span>{selected.quote.slice(0, 50)}{selected.quote.length > 50 ? "…" : ""}</span>
      <button disabled={!editable || dirty} onMouseDown={event => event.preventDefault()} onClick={() => void add()}>{t("reader.add_highlight")}</button>
      <button onMouseDown={event => event.preventDefault()} onClick={() => setSelected(null)}>{t("reader.cancel")}</button>
    </div>}
    <aside hidden={!open} className="reader-notebook reader-side-panel" aria-label="Notebook">
      <header><strong>Notebook <small>{record?.highlights.length ?? 0}</small></strong><button onClick={() => callbacks.current.onOpen(false)} aria-label={t("reader.close_notebook")}>×</button></header>
      <p role="status" data-testid="notebook-status">{t(busy || (!record && !error) ? "reader.notebook_saving" : error ? "reader.notebook_failed" : dirty ? "reader.notebook_unsaved" : "reader.notebook_saved")}</p>
      {Boolean(error) && <div role="alert" className="notebook-error"><p>{t(auth ? "reader.auth_error" : conflict ? "reader.notebook_conflict" : "reader.notebook_error")}</p>
        {!conflict && <button disabled={busy} onClick={() => pending.current ? void persist() : void load()}>{t("reader.retry_save")}</button>}
        {auth && <a href="/login" target="_blank" rel="noreferrer">{t("reader.login")}</a>}
        <button disabled={busy} onClick={() => void load()}>{t("reader.reload_notebook")}</button>
      </div>}
      {hint && <p role="alert">{hint}</p>}
      <button className="notebook-book-note" aria-pressed={!active} onClick={() => switchTo(null)}>{t("reader.book_note")}</button>
      <div className="notebook-editor">
        <label htmlFor="reader-note">{t(active ? "reader.highlight_note" : "reader.book_note")}</label>
        {activeItem && <blockquote>{activeItem.anchor.quote}</blockquote>}
        <textarea id="reader-note" value={draft} disabled={!editable} maxLength={active ? 20000 : 50000} onChange={event => setDraft(event.target.value)} placeholder={t("reader.note_placeholder")} />
        <button disabled={!editable || !dirty} onClick={() => record && void persist(active ? {...record, highlights: record.highlights.map(item => item.id === active ? {...item, note: draft} : item)} : {...record, book_note: draft})}>{t("reader.save_note")}</button>
        {dirty && <button disabled={!editable} onClick={() => {setDraft(original); setHint("");}}>{t("reader.discard_note")}</button>}
      </div>
      <div className="notebook-export"><button disabled={!editable || dirty || exporting} onClick={() => void exportNotes("markdown")}>{t("reader.export_markdown")}</button><button disabled={!editable || dirty || exporting} onClick={() => void exportNotes("json")}>{t("reader.export_json")}</button></div>
      <p>{t("reader.export_hint")}</p>
      {exportFailed && <p role="alert">{t("reader.export_error")}</p>}
      <p className="notebook-help">{t("reader.selection_limit")}</p>
      <div className="notebook-highlights">{record?.highlights.slice().sort((a,b) => a.anchor.index-b.anchor.index || a.anchor.offset-b.anchor.offset).map(item => <article key={item.id} data-highlight-id={item.id} className={active === item.id ? "active" : ""} style={{borderInlineStartColor: colors[item.color]}}>
        <button className="notebook-quote" onClick={() => void jump(item)} aria-label={t("reader.jump_highlight")}>{item.anchor.quote}</button>
        {invalid[item.id] && <p role="status">{t("reader.highlight_unresolved")}</p>}
        {item.note && <p className="notebook-note">{item.note}</p>}
        <div className="notebook-item-actions"><select aria-label={t("reader.highlight_color")} disabled={!editable || dirty} value={item.color} onChange={event => record && void persist({...record, highlights: record.highlights.map(h => h.id === item.id ? {...h, color: event.target.value as ReaderHighlight["color"]} : h)})}>
          {Object.keys(colors).map(color => <option value={color} key={color}>{t(`reader.color_${color}`)}</option>)}
        </select><button disabled={!editable || dirty} onClick={() => {switchTo(item.id); setDeleting(item.id);}}>{t("reader.delete_highlight")}</button></div>
        {deleting === item.id && <div><span>{t("reader.delete_highlight_hint")}</span><button disabled={!editable} onClick={() => record && void persist({...record, highlights: record.highlights.filter(h => h.id !== item.id)}, null)}>{t("reader.confirm_delete")}</button><button onClick={() => setDeleting(null)}>{t("reader.cancel")}</button></div>}
      </article>)}</div>
    </aside>
  </>;
}
