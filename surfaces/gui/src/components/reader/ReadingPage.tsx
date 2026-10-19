import {useEffect, useLayoutEffect, useRef, useState} from "react";
import {useTranslation} from "react-i18next";
import {getReaderOriginal, getReaderReading, ReaderRequestError, type ReaderBook, type ReaderPosition, type ReaderPreferences, type ReaderReading} from "../../api";
import type {FoliateView, ReaderBookEngine, TocEntry} from "./engine";
import {CONTENT_RULE, makeSafeEPUB} from "./safe-epub";
import {capturePosition, verifyPosition} from "./position";
import {ReadingWriter} from "./reading-writer";
import "foliate-js/view.js";
import Notebook from "./Notebook";
import BookSearch from "./BookSearch";

export type ReaderExitGuard = () => Promise<boolean>;
const defaults: ReaderPreferences = {font_size: 20, contents_open: true};

export default function ReadingPage({book, noteTarget, onTargetHandled, onError, onExitGuard}: {
  book: ReaderBook; noteTarget: string | null; onTargetHandled: () => void; onError: (error: unknown) => void;
  onExitGuard: (guard: ReaderExitGuard | null) => void;
}) {
  const {t} = useTranslation();
  const callbacks = useRef({onError, onExitGuard}); callbacks.current = {onError, onExitGuard};
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<FoliateView | null>(null);
  const writer = useRef<ReadingWriter | null>(null);
  const anchor = useRef<ReaderPosition | null>(null);
  const preferences = useRef<ReaderPreferences>(defaults);
  const startOver = useRef(false);
  const notebookGuard = useRef<(() => boolean) | null>(null);
  const [notebookOpen, setNotebookOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const openNotebook = (open: boolean) => {setNotebookOpen(open); if (open) setSearchOpen(false);};
  const [epoch, setEpoch] = useState(0);
  const [prefs, setPrefs] = useState(defaults);
  const [toc, setToc] = useState<TocEntry[]>([]);
  const [ready, setReady] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const navigationBusy = useRef(false);
  const [reading, setReading] = useState<ReaderReading | null>(null);
  const [saveState, setSaveState] = useState("unchanged");
  const [saveError, setSaveError] = useState<unknown>(null);
  const [anchorError, setAnchorError] = useState<"restore" | "capture" | null>(null);
  const applyStyle = () => {
    if (!host.current) return;
    const style = getComputedStyle(host.current);
    view.current?.renderer?.setStyles?.(`html,body {font-size:${preferences.current.font_size}px !important;line-height:1.7 !important;color:${style.color} !important;background:${style.backgroundColor} !important}`);
  };
  async function navigate(action: () => Promise<unknown> | undefined) {
    if (navigationBusy.current) return;
    navigationBusy.current = true; setNavigating(true);
    try {await action();} catch (error) {callbacks.current.onError(error);}
    finally {navigationBusy.current = false; setNavigating(false);}
  }

  useLayoutEffect(() => {
    let live = true, accepting = false, invalidAnchor = false;
    let engine: ReaderBookEngine | undefined;
    let session: ReadingWriter | undefined;
    const restart = startOver.current; startOver.current = false;
    const abort = new AbortController();
    const element = document.createElement("foliate-view") as FoliateView;
    element.addEventListener("external-link", event => event.preventDefault());
    element.addEventListener("link", event => {
      event.preventDefault();
      void navigate(() => element.goTo((event as CustomEvent).detail.href));
    });
    host.current!.append(element); view.current = element;
    setReady(false); setToc([]); setAnchorError(null); setSaveError(null); setSaveState("opening");
    const changed = () => {
      if (!live || !session) return;
      setReading(session.record); setSaveError(session.error);
      setSaveState(!accepting ? "opening" : session.error ? "failed" : session.dirty ? "saving" : session.record.updated_at ? "saved" : "unchanged");
    };
    callbacks.current.onExitGuard(async () => {
      if (notebookGuard.current && !notebookGuard.current()) return false;
      if (invalidAnchor) return false;
      return session ? session.flush() : true;
    });
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (session?.dirty || invalidAnchor || (notebookGuard.current && !notebookGuard.current())) {event.preventDefault(); event.returnValue = "";}
    };
    window.addEventListener("beforeunload", beforeUnload);
    const saveLocation = (event: Event) => {
      const reason = (event as CustomEvent).detail.reason;
      // Reflow emits 'anchor': changing a viewport is not reading farther.
      if (!accepting || !["navigation", "page", "snap", "scroll"].includes(reason)) return;
      try {
        anchor.current = capturePosition(element);
        invalidAnchor = false; setAnchorError(null);
        session!.update(anchor.current, preferences.current);
      } catch {
        invalidAnchor = true; setAnchorError("capture");
      }
    };
    let restoring = false;
    const opening = (async () => {
      if (book.content_rule !== CONTENT_RULE) throw new Error(t("reader.version_error"));
      const [state, original] = await Promise.all([getReaderReading(book.id), getReaderOriginal(book.id, abort.signal)]);
      if (!live) return;
      if (state.content_version !== book.content_version) throw new Error(t("reader.version_error"));
      session = new ReadingWriter(book.id, state, changed); writer.current = session;
      preferences.current = state.preferences; setPrefs(state.preferences); changed();
      engine = await makeSafeEPUB(original);
      if (!live) return;
      await element.open(engine);
      if (!live) return;
      element.renderer!.setAttribute("max-column-count", "1");
      element.renderer!.addEventListener("relocate", saveLocation);
      applyStyle();
      // React must first apply the restored TOC layout before the first pagination.
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      if (!live) return;
      const saved = restart ? null : state.position;
      if (saved) {
        restoring = true;
        const chapter = engine.sections[saved.index];
        if (!chapter) throw new Error("Missing saved chapter");
        verifyPosition(element, saved, await chapter.createDocument());
      }
      await element.init(saved ? {lastLocation: saved.cfi} : {showTextStart: true});
      if (!live) return;
      if (saved) {
        const content = element.renderer!.getContents().find(item => item.index === saved.index);
        if (!content) throw new Error("Saved chapter not displayed");
        verifyPosition(element, saved, content.doc);
      }
      restoring = false;
      anchor.current = saved ?? capturePosition(element);
      setToc(engine.toc ?? []); accepting = true; setReady(true);
      changed();
      if (restart) session.update(anchor.current, preferences.current);
    })().catch(error => {
      if (!live) return;
      // A failed restore has not changed anything locally; leaving is safe.
      if (restoring) setAnchorError("restore");
      else callbacks.current.onError(error);
    });
    return () => {
      live = false; accepting = false; abort.abort();
      window.removeEventListener("beforeunload", beforeUnload);
      callbacks.current.onExitGuard(null);
      writer.current = null; view.current = null;
      // Layout cleanup runs before React detaches the host. Stop observers now;
      // an open() already in flight may finish later and needs the same cleanup.
      const dispose = () => {element.close(); engine?.destroy(); element.remove();};
      dispose();
      void opening.finally(dispose);
    };
  }, [book.id, epoch]);

  useEffect(applyStyle, [prefs.font_size, ready]);
  function updatePreferences(next: ReaderPreferences) {
    preferences.current = next; setPrefs(next);
    writer.current?.update(anchor.current, next);
  }
  const entries = (items: TocEntry[], depth = 0): React.ReactNode => items.map((item, index) => <div key={`${item.href}-${index}`}>
    <button disabled={!ready || navigating} style={{paddingInlineStart: 12 + depth * 12}} onClick={() => void navigate(() => view.current?.goTo(item.href))}>{item.label}</button>
    {item.subitems && entries(item.subitems, depth + 1)}
  </div>);
  const restartReading = (reset = false) => {
    if (notebookGuard.current && !notebookGuard.current()) return;
    startOver.current = reset; setEpoch(value => value + 1);
  };
  const conflict = saveError instanceof ReaderRequestError && saveError.status === 409;
  const auth = saveError instanceof ReaderRequestError && [401,403].includes(saveError.status);
  return <>
    <div className="reader-reading-tools">
      <button disabled={!ready || navigating} aria-expanded={prefs.contents_open} onClick={() => updatePreferences({...prefs, contents_open: !prefs.contents_open})}>{t("reader.contents")}</button>
      <label>{t("reader.font_size")} <select disabled={!ready || navigating} value={prefs.font_size} onChange={event => updatePreferences({...prefs, font_size: Number(event.target.value) as ReaderPreferences["font_size"]})}>
        {[16,20,24,28,32].map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <button aria-expanded={notebookOpen} onClick={() => openNotebook(!notebookOpen)}>Notebook</button>
      <button disabled={!ready} aria-expanded={searchOpen} onClick={() => {setSearchOpen(value => !value); setNotebookOpen(false);}}>{t("reader.book_search")}</button>
      <div className="reader-page-actions"><button disabled={!ready || navigating} onClick={() => void navigate(() => view.current?.prev())}>{t("reader.previous")}</button><button disabled={!ready || navigating} onClick={() => void navigate(() => view.current?.next())}>{t("reader.next")}</button></div>
    </div>
    <div className="reader-reading-status">
      <span data-testid="reader-save-status" role="status" aria-live="polite">{t(`reader.save_${anchorError === "restore" ? "restore_failed" : anchorError ? "failed" : saveState}`)}</span>
      <span data-testid="reader-progress" title={t("reader.progress_hint")}>{t("reader.furthest", {percent: ((reading?.progress ?? 0) * 100).toFixed(1)})}</span>
    </div>
    {Boolean(saveError) && <div className="reader-message" role="alert"><span>{t(auth ? "reader.auth_error" : conflict ? "reader.reading_conflict" : "reader.save_error")}</span>
      {auth && <a href="/login" target="_blank" rel="noreferrer">{t("reader.login")}</a>}
      {!conflict && <button onClick={() => void writer.current?.retry()}>{t("reader.retry_save")}</button>}
      <button onClick={() => restartReading()}>{t("reader.use_server")}</button>
    </div>}
    {anchorError && <div className="reader-message" role="alert"><span>{t(`reader.${anchorError}_error`)}</span>
      <button onClick={() => restartReading()}>{t("reader.use_server")}</button>
      {anchorError === "restore" && <button onClick={() => restartReading(true)}>{t("reader.start_over")}</button>}
    </div>}
    {!ready && !anchorError && <p role="status">{t("reader.loading")}</p>}
    <div className="reader-body-layout">
    <div className={`reader-reading ${prefs.contents_open ? "with-contents" : ""}`}>
      {prefs.contents_open && <nav aria-label={t("reader.contents")}>{entries(toc)}</nav>}
      <div className="reader-page" ref={host} aria-label={t("reader.body")} />
    </div>
    <Notebook key={epoch} book={book} view={ready ? view.current : null} open={notebookOpen} onOpen={openNotebook} target={noteTarget} onTargetHandled={onTargetHandled}
      onGuard={guard => {notebookGuard.current = guard;}} onNavigate={cfi => navigate(() => view.current?.goTo(cfi))} />
    <BookSearch key={`search-${epoch}`} view={ready ? view.current : null} open={searchOpen} onClose={() => setSearchOpen(false)} onNavigate={cfi => navigate(() => view.current?.goTo(cfi))} />
    </div>
  </>;
}
