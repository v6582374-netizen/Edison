import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadReaderOriginal, getReaderBook, getReaderBooks, importReaderBook, placeReaderBook, ReaderRequestError, type ReaderBook } from "../../api";
import { Icon } from "../Icon";
import ReadingPage from "./ReadingPage";
import LibrarySearch from "./LibrarySearch";
import type {ReaderExitGuard} from "./ReadingPage";
import "./reader.css";

export default function ReaderView({ bookId, noteTarget, onTargetHandled, onOpenBook, onExitGuard }: {bookId: string | null; noteTarget: string | null; onTargetHandled: () => void; onOpenBook: (id: string | null, note?: string | null) => void; onExitGuard: (guard: ReaderExitGuard | null) => void}) {
  const { t, i18n } = useTranslation();
  const exitGuard = useRef<ReaderExitGuard | null>(null);
  const [books, setBooks] = useState<ReaderBook[] | null>(null);
  const [book, setBook] = useState<ReaderBook | null>(null);
  const [filter, setFilter] = useState<"all" | ReaderBook["location"]>("all");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{auth: boolean; message: string} | null>(null);
  const [notice, setNotice] = useState("");
  const fail = (cause: unknown) => {
    const auth = cause instanceof ReaderRequestError && [401,403].includes(cause.status);
    const conflict = cause instanceof ReaderRequestError && cause.status === 409;
    setError({auth, message: auth ? t("reader.auth_error") : conflict ? t("reader.conflict") : i18n.language.startsWith("zh") && cause instanceof Error ? cause.message : t("reader.error")});
  };
  useEffect(() => {
    let live = true; setError(null);
    if (bookId) {
      setBook(null);
      getReaderBook(bookId).then(value => { if (live) setBook(value); }).catch(error => { if (live) fail(error); });
    } else getReaderBooks().then(value => { if (live) setBooks(value.books); }).catch(error => { if (live) fail(error); });
    return () => { live = false; };
  }, [bookId, refresh]);
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true); setError(null); setNotice("");
    try {
      const result = await importReaderBook(file);
      setNotice(t(result.created ? "reader.imported" : "reader.duplicate"));
      setRefresh(value => value + 1);
    } catch (error) { fail(error); } finally { setBusy(false); }
  }
  async function place(item: ReaderBook, location: ReaderBook["location"]) {
    setBusy(true); setError(null);
    try {
      const saved = await placeReaderBook(item, location);
      setBooks(items => items?.map(value => value.id === saved.id ? saved : value) ?? null);
      setBook(value => value?.id === saved.id ? saved : value);
    } catch (error) { fail(error); } finally { setBusy(false); }
  }
  const placement = (item: ReaderBook) => <select disabled={busy} aria-label={t("reader.placement", {title: item.title})} value={item.location} onChange={event => place(item, event.target.value as ReaderBook["location"])}>
    {(["inbox","later","archive"] as const).map(location => <option value={location} key={location}>{t(`reader.${location}`)}</option>)}
  </select>;
  const filtered = books?.filter(item => (filter === "all" || item.location === filter) && `${item.title} ${item.author}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <main className="reader-workspace" aria-label="Reader">
    <header className="reader-header">
      <div className="reader-heading">{bookId && <button onClick={() => onOpenBook(null)} className="reader-back">← {t("reader.library")}</button>}<h1>{bookId ? book?.title ?? t("reader.loading") : "Reader"}</h1>{book && bookId && <p>{book.author}</p>}</div>
      <div className="reader-actions">{book && bookId ? <>{placement(book)}<button onClick={() => downloadReaderOriginal(book).catch(fail)}>{t("reader.download")}</button></> : <label className="reader-import">{busy ? t("reader.importing") : t("reader.import")}<input aria-label={t("reader.import")} type="file" accept=".epub,application/epub+zip" disabled={busy} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ""; }} /></label>}</div>
    </header>
    {error && <div className="reader-message" role="alert"><span>{error.message}</span>{error.auth ? <a href="/login">{t("reader.login")}</a> : <button onClick={async () => { if (!exitGuard.current || await exitGuard.current()) setRefresh(value => value + 1); }}>{t("reader.retry")}</button>}</div>}
    {bookId ? book && <ReadingPage key={`${book.id}-${refresh}`} book={book} noteTarget={noteTarget} onTargetHandled={onTargetHandled} onError={fail} onExitGuard={guard => { exitGuard.current = guard; onExitGuard(guard); }} /> : <>
      <div className="reader-library-tools"><nav aria-label={t("reader.locations")}>{(["all","inbox","later","archive"] as const).map(location => <button key={location} aria-pressed={filter === location} onClick={() => setFilter(location)}>{t(`reader.${location}`)} <span>{books?.filter(item => location === "all" || item.location === location).length ?? 0}</span></button>)}</nav><input aria-label={t("reader.search")} placeholder={t("reader.search")} value={query} onChange={event => setQuery(event.target.value)} /></div>
      <LibrarySearch onOpen={onOpenBook} />
      {notice && <p className="reader-notice" role="status">{notice}</p>}
      {!books && !error && <p role="status">{t("reader.loading")}</p>}
      <div className="reader-books">{filtered?.map(item => <article className="reader-book-row" data-book-id={item.id} key={item.id}>
        <button onClick={() => onOpenBook(item.id)} className="reader-book-open"><span className="reader-book-symbol"><Icon name="book" size={22} /></span><span><strong>{item.title}</strong><small>{item.author || t("reader.unknown_author")}</small></span></button>
        {placement(item)}
      </article>)}{filtered?.length === 0 && <div className="reader-empty"><Icon name="book" size={30} /><h2>{t(books?.length ? "reader.no_matches" : "reader.empty")}</h2>{!books?.length && <p>{t("reader.empty_hint")}</p>}</div>}</div>
    </>}
  </main>;
}
