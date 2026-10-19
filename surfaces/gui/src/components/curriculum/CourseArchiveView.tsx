import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadClassroomDocument, getClassroomDocument, getCourseArchive, prepareClassroomDocument } from "../../api";
import type { ClassroomDocument, CourseArchiveSnapshot, RecordedClass } from "../../api";
import { Icon } from "../Icon";
import { ArchiveStatus } from "./ArchiveStatus";
import "./archive.css";

const ClassroomMarkdown = lazy(() => import("./ClassroomMarkdown"));

export function CourseArchiveView({ documentId, onOpenDocument, onTimetable }: {
  documentId: string | null; onOpenDocument: (id: string | null) => void; onTimetable: () => void;
}) {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<CourseArchiveSnapshot | null>(null);
  const [doc, setDoc] = useState<ClassroomDocument | null>(null);
  const [course, setCourse] = useState("");
  const [showWaiting, setShowWaiting] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<false | "auth" | "general">(false);
  const failed = (cause: unknown) => setError(/Course archive: (401|403)/.test(String(cause)) ? "auth" : "general");
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getCourseArchive().then(data => { if (live) { setSnapshot(data); setError(false); } }).catch(cause => { if (live) failed(cause); });
    return () => { live = false; };
  }, [refresh]);
  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(false);
    if (documentId) getClassroomDocument(documentId).then(value => { if (live) setDoc(value); }).catch(cause => { if (live) failed(cause); });
    return () => { live = false; };
  }, [documentId]);
  const working = snapshot?.recordings.some(record => ["pending", "generating"].includes(record.state));
  useEffect(() => {
    if ((!working && !snapshot?.automation?.enabled) || documentId || error === "auth") return;
    const timer = window.setInterval(() => setRefresh(value => value + 1), working ? 5000 : 30000);
    return () => window.clearInterval(timer);
  }, [working, documentId, snapshot?.automation?.enabled, error]);
  const prepare = async (record: RecordedClass) => {
    setPending(record.id);
    setError(false);
    try { await prepareClassroomDocument(record.id); setRefresh(value => value + 1); }
    catch (cause) { failed(cause); }
    finally { setPending(null); }
  };
  const download = async () => {
    if (!doc) return;
    try { await downloadClassroomDocument(doc.id, doc.title); } catch (cause) { failed(cause); }
  };
  const ready = snapshot?.recordings.filter(record => record.documents.length).length ?? 0;
  const waiting = snapshot?.recordings.filter(record => record.state === "needs_transcript") ?? [];
  const remaining = (snapshot?.recordings.length ?? 0) - ready - waiting.filter(record => !record.documents.length).length;
  const selectedCourse = snapshot?.courses.find(item => item.key === course) ?? snapshot?.courses[0];
  const courseRecords = snapshot?.recordings.filter(record => record.course === selectedCourse?.key) ?? [];
  const records = [...(showWaiting ? waiting : courseRecords)].sort((a, b) => b.date.localeCompare(a.date) || b.first - a.first);
  const sharedMetadata = new Set(courseRecords.map(record => `${record.teacher} · ${record.room.replace(/^城市学院/, "")}`));
  const automationErrors = Object.values(snapshot?.automation?.errors ?? {});
  if (snapshot?.automation?.generation_error) automationErrors.push(snapshot.automation.generation_error);
  const chooseCourse = (key: string) => { setCourse(key); setShowWaiting(false); };
  useEffect(() => { list.current?.scrollTo({ top: 0 }); }, [course, showWaiting]);
  const related = snapshot?.recordings.find(record => record.id === doc?.recording_id);
  const versions = related ? [...related.documents, ...(related.history ?? [])] : [];
  const coverage = Object.values(snapshot?.coverage ?? {});
  const from = coverage.map(item => item.from).sort()[0] ?? "2026-09-14";
  const checkedAt = coverage.map(item => item.checked_at).filter((value): value is string => Boolean(value)).sort().slice(-1)[0];
  const through = checkedAt ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(checkedAt)) : coverage.map(item => item.through).sort().slice(-1)[0] ?? "2026-10-10";
  return <main className="course-archive" aria-label={t("course_archive.title")}>
    <header className="course-archive-header">
      <div>
        <p className="course-archive-eyebrow">Curriculum <span>/ {t("course_archive.title")}</span></p>
        <h1>{doc ? doc.title : t("course_archive.title")}</h1>
        <p className="course-archive-description" aria-live={documentId ? undefined : "polite"}>{documentId ? t("course_archive.reading_note") : snapshot ? <>{t("course_archive.readable_count", { count: ready })}<span className="course-archive-separator">·</span>{t("course_archive.waiting_count", { count: waiting.length })}{remaining > 0 && <><span className="course-archive-separator">·</span>{t("course_archive.remaining_count", { count: remaining })}</>}</> : t("course_archive.description")}</p>
      </div>
      <div className="course-archive-actions">
        <button onClick={() => documentId ? onOpenDocument(null) : onTimetable()}><Icon name="chevronRight" className="curriculum-back" size={14} />{t(documentId ? "course_archive.back" : "course_archive.timetable")}</button>
        {doc && <button onClick={download}><Icon name="file" size={14} />{t("course_archive.download")}</button>}
      </div>
    </header>
    {error && <div className="course-archive-message" role="alert">{t(error === "auth" ? "course_archive.auth_error" : "course_archive.error")} {error === "auth" ? <a href="/login">{t("course_archive.sign_in")}</a> : <button onClick={() => { setRefresh(value => value + 1); if (documentId) { setError(false); getClassroomDocument(documentId).then(setDoc).catch(failed); } }}>{t("course_archive.retry")}</button>}</div>}
    {documentId ? <>
      {doc ? <div className="course-archive-reader-scroll">
        {related && <div className="course-archive-source">
          <span>{related.date} · {related.teacher} · {related.room}</span>
          <a href={related.source_url} target="_blank" rel="noreferrer">{t("course_archive.recording")} ↗</a>
          {versions.length > 1 && <select aria-label={t("course_archive.perspective")} value={documentId} onChange={event => onOpenDocument(event.target.value)}>{versions.map(version => <option key={version.id} value={version.id}>{version.title} · {new Date(version.created_at * 1000).toLocaleString()}</option>)}</select>}
        </div>}
        <article className="course-archive-reader" aria-label={t("course_archive.document")}><Suspense fallback={<p role="status">{t("course_archive.loading")}</p>}><ClassroomMarkdown text={doc.markdown} /></Suspense></article>
      </div> : !error && <p role="status">{t("course_archive.loading")}</p>}
    </> : <>
      {snapshot && <ArchiveStatus snapshot={snapshot} />}
      {snapshot?.backup?.state === "conflict" && <div className="course-archive-attention" role="alert"><Icon name="warning" size={16} /><p>{t("course_archive.backup_detail_conflict")} {t("course_archive.conflicts", { count: snapshot.backup.conflicts?.length ?? 0 })}</p></div>}
      {!!automationErrors.length && <div className="course-archive-attention" role="alert"><Icon name="warning" size={16} /><div>{automationErrors.map((message, index) => <p key={index}>{message}</p>)}</div></div>}
      <div className="course-archive-workspace">
        <nav className="course-archive-courses" aria-label={t("course_archive.course_navigation")}>
          <p>{t("course_archive.courses_count", { count: snapshot?.courses.length ?? 0 })}</p>
          {snapshot?.courses.map(item => <button key={item.key} aria-pressed={!showWaiting && selectedCourse?.key === item.key} onClick={() => chooseCourse(item.key)}>
            <span>{item.name}</span><span>{snapshot.recordings.filter(record => record.course === item.key).length}</span>
          </button>)}
        </nav>
        <div className="course-archive-content">
          <div className="course-archive-mobile-picker"><label>{t("course_archive.course")}<select aria-label={t("course_archive.course")} value={selectedCourse?.key ?? ""} onChange={event => chooseCourse(event.target.value)}>
            {snapshot?.courses.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}
          </select></label></div>
          <div className="course-archive-toolbar">
            <div className="course-archive-course-heading"><h2>{showWaiting ? t("course_archive.waiting_title") : selectedCourse?.name}</h2>
              <p>{!showWaiting && sharedMetadata.size === 1 && <>{[...sharedMetadata][0]}<span className="course-archive-separator">·</span></>}{snapshot && t("course_archive.classes_count", { count: records.length })}</p>
            </div>
            <button className="course-archive-refresh" aria-label={t("course_archive.refresh")} onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" size={15} /></button>
          </div>
          {!!waiting.length && <button className="course-archive-waiting-toggle" aria-pressed={showWaiting} onClick={() => setShowWaiting(value => !value)}><Icon name="clock" size={15} /><span>{showWaiting ? t("course_archive.return_course") : t("course_archive.waiting_action", { count: waiting.length })}</span><Icon name="chevronRight" size={14} /></button>}
          <div ref={list} className="course-archive-list" aria-label={t("course_archive.class_list")}>
            {!snapshot && !error && <p role="status">{t("course_archive.loading")}</p>}
            {snapshot && !snapshot.recordings.length && <div className="course-archive-empty"><Icon name="book" size={28} /><h2>{t("course_archive.empty")}</h2><p>{t("course_archive.empty_note")}</p></div>}
            {snapshot && !!snapshot.recordings.length && !records.length && <p className="course-archive-muted">{t(showWaiting ? "course_archive.no_waiting" : snapshot.coverage[selectedCourse?.key ?? ""]?.complete ? "course_archive.not_listed" : "course_archive.not_checked")}</p>}
            {records.map(record => <div key={record.id} className="course-archive-row">
              <time dateTime={record.date} aria-label={record.date}>{record.date.slice(5)}</time>
              <div className="course-archive-row-main">
                {showWaiting && <p className="course-archive-row-course">{record.name}</p>}
                {record.documents.length ? record.documents.map(version => <button className="course-archive-document-link" key={version.id} aria-label={`${record.title} ${t(version.perspective === "transcript" ? "course_archive.transcript" : "course_archive.exam_notes")}`} onClick={() => onOpenDocument(version.id)}><span className="course-archive-period">{t("course_archive.period", { first: record.first, last: record.last })}</span><span>{t(version.perspective === "transcript" ? "course_archive.transcript" : "course_archive.exam_notes")}</span></button>) : <strong>{t("course_archive.period", { first: record.first, last: record.last })}</strong>}
                {(showWaiting || sharedMetadata.size > 1) && <p>{record.teacher} · {record.room.replace(/^城市学院/, "")}</p>}
                {record.detail && <small>{record.detail}</small>}
              </div>
              <div className="course-archive-row-actions">
                {!!record.history?.length && <button className="course-archive-history" onClick={() => onOpenDocument(record.history![0].id)}>{t("course_archive.history_short", { count: record.history.length })}</button>}
                {record.state === "ready" && record.documents.length ? <span className="course-archive-ready-icon" aria-label={t("course_archive.states.ready")}><Icon name="file" size={16} /></span> : <span className={`course-archive-state is-${record.state}`}>{t(`course_archive.states.${record.state}`, { defaultValue: record.state })}</span>}
                {!record.documents.length && ["pending", "failed"].includes(record.state) && <button disabled={pending === record.id} className="course-archive-prepare" onClick={() => prepare(record)}>{t("course_archive.prepare")}</button>}
              </div>
            </div>)}
          </div>
        </div>
      </div>
      <footer>{t("course_archive.scope_note", { from, through })}</footer>
    </>}
  </main>;
}
