import {useEffect, useRef, useState} from "react";
import {useTranslation} from "react-i18next";
import {searchReaderNotes, type ReaderNoteResults} from "../../api";
export default function LibrarySearch({onOpen}: {onOpen: (id: string, note: string) => void}) {
  const {t} = useTranslation();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<ReaderNoteResults | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const controller = useRef<AbortController>();
  useEffect(() => () => controller.current?.abort(), []);
  const clear = () => {controller.current?.abort(); setResult(null); setBusy(false); setFailed(false);};
  async function search(offset = 0) {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setBusy(true); setFailed(false);
    try {
      const data = await searchReaderNotes(query, offset, current.signal);
      if (!current.signal.aborted) setResult(data);
    } catch {if (!current.signal.aborted) {setFailed(true); setResult(null);}}
    finally {if (!current.signal.aborted) setBusy(false);}
  }
  return <section className="reader-library-search" aria-label={t("reader.notes_search")}>
    <form onSubmit={event => {event.preventDefault(); void search();}}>
      <input aria-label={t("reader.notes_query")} placeholder={t("reader.notes_query")} maxLength={200} value={query} onChange={event => {clear(); setQuery(event.target.value);}} />
      <button disabled={busy || !query.trim()}>{t("reader.find_notes")}</button>
      {result && <button type="button" onClick={clear}>{t("reader.clear_results")}</button>}
    </form>
    <p>{t("reader.notes_search_hint")}</p>
    {busy && <p role="status">{t("reader.search_working")}</p>}
    {failed && <p role="alert">{t("reader.notes_search_error")}</p>}
    {result && <div className="reader-note-results"><p role="status">{t("reader.notes_found",{count:result.total})}</p>
      {result.results.map(item => <button className="reader-search-hit" key={`${item.book_id}-${item.kind}-${item.highlight_id}`} onClick={() => onOpen(item.book_id,item.highlight_id ?? "book")}>
        <strong>{item.title}</strong><small>{t(`reader.${item.kind}`)} · {t(`reader.${item.location}`)}</small><span>{item.excerpt}</span>
      </button>)}
      <div className="reader-result-pages"><button disabled={busy || result.offset === 0} onClick={() => void search(Math.max(0,result.offset-50))}>{t("reader.previous_results")}</button><button disabled={busy || result.offset+result.results.length>=result.total} onClick={() => void search(result.offset+50)}>{t("reader.next_results")}</button></div>
    </div>}
  </section>;
}
