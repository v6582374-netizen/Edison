import {useEffect, useRef, useState} from "react";
import {useTranslation} from "react-i18next";
import {searchMatcher} from "foliate-js/search.js";
import {textWalker} from "foliate-js/text-walker.js";
import {Overlayer} from "foliate-js/overlayer.js";
import type {ReaderPosition} from "../../api";
import type {FoliateView} from "./engine";
import {captureHighlight, verifyPosition} from "./position";

const overlayKey = "edison-text-search";
interface Match {anchor: ReaderPosition; before: string; after: string}
export default function BookSearch({view, open, onClose, onNavigate}: {
  view: FoliateView | null; open: boolean; onClose: () => void; onNavigate: (cfi: string) => Promise<void>;
}) {
  const {t} = useTranslation();
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [selected, setSelected] = useState<ReaderPosition | null>(null);
  const [status, setStatus] = useState<"idle" | "working" | "done" | "limited" | "failed">("idle");
  const [jumpError, setJumpError] = useState(false);
  const [jumping, setJumping] = useState(false);
  const run = useRef(0);
  const stop = () => {run.current++; setStatus("idle"); setMatches([]); setSelected(null); setJumpError(false); setJumping(false);};
  useEffect(() => {stop(); return () => {run.current++;};}, [view]);
  useEffect(() => {if (!open) {run.current++; if (status === "working") {setStatus("idle"); setMatches([]);}}}, [open]);
  useEffect(() => {
    if (!view || !selected) return;
    let live = true;
    const draw = () => queueMicrotask(() => {
      if (!live) return;
      try {
        const content = view.renderer?.getContents().find(item => item.index === selected.index);
        if (!content?.overlayer) return;
        verifyPosition(view, selected, content.doc);
        content.overlayer.remove(overlayKey);
        content.overlayer.add(overlayKey, view.resolveCFI(selected.cfi).anchor(content.doc), Overlayer.outline);
      } catch {setJumpError(true);}
    });
    draw(); view.addEventListener("create-overlay", draw);
    return () => {
      live = false; view.removeEventListener("create-overlay", draw);
      for (const content of view.renderer?.getContents() ?? []) content.overlayer?.remove(overlayKey);
    };
  }, [view, selected]);
  async function search() {
    if (!view?.book || !query.trim()) return;
    const ticket = ++run.current;
    setMatches([]); setSelected(null); setStatus("working"); setJumpError(false);
    const found: Match[] = [];
    const matcher = searchMatcher(textWalker, {matchCase: true, matchDiacritics: true});
    try {
      for (const [index, section] of view.book.sections.entries()) {
        const doc = await section.createDocument();
        if (ticket !== run.current) return;
        for (const result of matcher(doc, query)) {
          if (ticket !== run.current) return;
          const anchor = captureHighlight(view, index, result.range);
          // Derive the displayed quote from the verified DOM range, never from
          // a separately normalized string with potentially different offsets.
          if (anchor.quote !== query) throw new Error("Search range differs from query");
          const text = doc.body.textContent ?? "";
          found.push({anchor, before: text.slice(Math.max(0, anchor.offset-45),anchor.offset), after: text.slice(anchor.offset+anchor.quote.length,anchor.offset+anchor.quote.length+65)});
          if (found.length === 200) {setMatches(found); setStatus("limited"); return;}
        }
        setMatches([...found]);
        await new Promise(resolve => setTimeout(resolve, 0));
        if (ticket !== run.current) return;
      }
      setStatus("done");
    } catch {if (ticket === run.current) {setMatches([]); setStatus("failed");}}
  }
  async function jump(match: Match) {
    if (!view || jumping) return;
    const ticket = run.current;
    setJumping(true); setJumpError(false);
    try {
      verifyPosition(view, match.anchor, await view.book!.sections[match.anchor.index].createDocument());
      if (ticket !== run.current) return;
      await onNavigate(match.anchor.cfi);
      if (ticket !== run.current) return;
      const content = view.renderer?.getContents().find(item => item.index === match.anchor.index);
      if (!content) throw new Error("Missing search chapter");
      verifyPosition(view, match.anchor, content.doc);
      const range = view.resolveCFI(match.anchor.cfi).anchor(content.doc);
      if (!range.getClientRects().length || view.lastLocation?.range.comparePoint(range.startContainer,range.startOffset) !== 0)
        throw new Error("Search origin is not visible");
      setSelected(match.anchor);
      // A narrow panel would hide the requested passage. Keep the query/results
      // accessible on wide layouts; on narrow ones reveal the reading surface.
      if (window.matchMedia("(max-width:1100px)").matches) onClose();
    } catch {if (ticket === run.current) setJumpError(true);}
    finally {setJumping(false);}
  }
  return <aside hidden={!open} className="reader-side-panel reader-text-search" aria-label={t("reader.book_search")}>
    <header><strong>{t("reader.book_search")}</strong><button aria-label={t("reader.close_search")} onClick={onClose}>×</button></header>
    <form onSubmit={event => {event.preventDefault(); void search();}}>
      <input aria-label={t("reader.book_query")} maxLength={200} value={query} onChange={event => {stop(); setQuery(event.target.value);}} />
      <button disabled={!view || !query.trim() || status === "working"} type="submit">{t("reader.find")}</button>
      {status === "working" && <button type="button" onClick={stop}>{t("reader.cancel")}</button>}
    </form>
    <p>{t("reader.literal_search")}</p>
    <p role="status" data-testid="text-search-status">{t(`reader.search_${status}`, {count:matches.length})}</p>
    {jumpError && <p role="alert">{t("reader.search_jump_error")}</p>}
    {matches.map((item, index) => <button key={item.anchor.cfi} className="reader-search-hit" data-search-cfi={item.anchor.cfi} disabled={jumping} onClick={() => void jump(item)}>
      <small>{t("reader.chapter_match", {chapter:item.anchor.index+1,number:index+1})}</small>
      <span>{item.before}<mark>{item.anchor.quote}</mark>{item.after}</span>
    </button>)}
  </aside>;
}
