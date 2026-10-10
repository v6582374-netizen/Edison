import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadClassroomDocument, getClassroomDocument, getCourseArchive, prepareClassroomDocument } from "../../api";
import type { ClassroomDocument, CourseArchiveSnapshot, RecordedClass } from "../../api";
import { Icon } from "../Icon";
import "./archive.css";

const ClassroomMarkdown = lazy(() => import("./ClassroomMarkdown"));

export function CourseArchiveView({ documentId, onOpenDocument, onTimetable }: {
  documentId: string | null; onOpenDocument: (id: string | null) => void; onTimetable: () => void;
}) {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<CourseArchiveSnapshot | null>(null);
  const [doc, setDoc] = useState<ClassroomDocument | null>(null);
  const [course, setCourse] = useState("");
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setError(false);
    getCourseArchive().then(data => { if (live) setSnapshot(data); }).catch(() => { if (live) setError(true); });
    return () => { live = false; };
  }, [refresh]);
  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(false);
    if (documentId) getClassroomDocument(documentId).then(value => { if (live) setDoc(value); }).catch(() => { if (live) setError(true); });
    return () => { live = false; };
  }, [documentId]);
  const working = snapshot?.recordings.some(record => ["pending", "generating"].includes(record.state));
  useEffect(() => {
    if (!working || documentId) return;
    const timer = window.setInterval(() => setRefresh(value => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [working, documentId]);
  const prepare = async (record: RecordedClass) => {
    setPending(record.id);
    setError(false);
    try { await prepareClassroomDocument(record.id); setRefresh(value => value + 1); }
    catch { setError(true); }
    finally { setPending(null); }
  };
  const download = async () => {
    if (!doc) return;
    try { await downloadClassroomDocument(doc.id, doc.title); } catch { setError(true); }
  };
  const ready = snapshot?.recordings.filter(record => record.documents.length).length ?? 0;
  const related = snapshot?.recordings.find(record => record.id === doc?.recording_id);
  const coverage = Object.values(snapshot?.coverage ?? {});
  const from = coverage.map(item => item.from).sort()[0] ?? "2026-09-14";
  const checkedAt = coverage.map(item => item.checked_at).filter((value): value is string => Boolean(value)).sort().slice(-1)[0];
  const through = checkedAt ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(checkedAt)) : coverage.map(item => item.through).sort().slice(-1)[0] ?? "2026-10-10";
  return <main className="course-archive" aria-label={t("course_archive.title")}>
    <header className="course-archive-header">
      <div>
        <p className="course-archive-eyebrow">Curriculum <span>/ {t("course_archive.title")}</span></p>
        <h1>{doc ? doc.title : t("course_archive.title")}</h1>
        <p className="course-archive-description">{documentId ? t("course_archive.reading_note") : t("course_archive.description")}</p>
      </div>
      <div className="course-archive-actions">
        <button onClick={() => documentId ? onOpenDocument(null) : onTimetable()}><Icon name="chevronRight" className="curriculum-back" size={14} />{t(documentId ? "course_archive.back" : "course_archive.timetable")}</button>
        {doc && <button onClick={download}><Icon name="file" size={14} />{t("course_archive.download")}</button>}
      </div>
    </header>
    {error && <div className="course-archive-message" role="alert">{t("course_archive.error")} <button onClick={() => { setRefresh(value => value + 1); if (documentId) { setError(false); getClassroomDocument(documentId).then(setDoc).catch(() => setError(true)); } }}>{t("course_archive.retry")}</button></div>}
    {documentId ? <>
      {doc ? <div className="course-archive-reader-scroll">
        {related && <div className="course-archive-source">
          <span>{related.date} · {related.teacher} · {related.room}</span>
          <a href={related.source_url} target="_blank" rel="noreferrer">{t("course_archive.recording")} ↗</a>
          {related.documents.length > 1 && <select aria-label={t("course_archive.perspective")} value={documentId} onChange={event => onOpenDocument(event.target.value)}>{related.documents.map(version => <option key={version.id} value={version.id}>{version.title}</option>)}</select>}
        </div>}
        <article className="course-archive-reader" aria-label={t("course_archive.document")}><Suspense fallback={<p role="status">{t("course_archive.loading")}</p>}><ClassroomMarkdown text={doc.markdown} /></Suspense></article>
      </div> : !error && <p role="status">{t("course_archive.loading")}</p>}
    </> : <>
      <div className="course-archive-toolbar">
        <label>{t("course_archive.course")}<select aria-label={t("course_archive.course")} value={course} onChange={event => setCourse(event.target.value)}>
          <option value="">{t("course_archive.all_courses")}</option>
          {snapshot?.courses.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}
        </select></label>
        <span aria-live="polite">{snapshot && t("course_archive.progress", { ready, total: snapshot.recordings.length })}</span>
        <button aria-label={t("course_archive.refresh")} onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" size={15} /></button>
      </div>
      <div className="course-archive-list">
        {!snapshot && !error && <p role="status">{t("course_archive.loading")}</p>}
        {snapshot && !snapshot.recordings.length && <div className="course-archive-empty"><Icon name="book" size={28} /><h2>{t("course_archive.empty")}</h2><p>{t("course_archive.empty_note")}</p></div>}
        {snapshot?.courses.filter(item => !course || item.key === course).map(item => {
          const records = snapshot.recordings.filter(record => record.course === item.key);
          return <section key={item.key} className="course-archive-group" aria-label={item.name}>
            <h2>{item.name}<span>{records.length}</span></h2>
            {!records.length && <p className="course-archive-muted">{t(snapshot.coverage[item.key]?.complete ? "course_archive.not_listed" : "course_archive.not_checked")}</p>}
            {records.map(record => <div key={record.id} className="course-archive-row">
              <time dateTime={record.date}>{record.date.slice(5)}</time>
              <div className="course-archive-row-main">
                {record.documents.length ? record.documents.map(version => <button className="course-archive-document-link" key={version.id} onClick={() => onOpenDocument(version.id)}>{record.title} <span>{t("course_archive.exam_notes")}</span><Icon name="chevronRight" size={14} /></button>) : <strong>{record.title}</strong>}
                <p>{record.teacher} · {record.room.replace(/^城市学院/, "")}</p>
                {!record.documents.length && record.detail && <small>{record.detail}</small>}
              </div>
              <span className={`course-archive-state is-${record.state}`}>{t(`course_archive.states.${record.state}`, { defaultValue: record.state })}</span>
              {!record.documents.length && ["pending", "failed"].includes(record.state) && <button disabled={pending === record.id} className="course-archive-prepare" onClick={() => prepare(record)}>{t("course_archive.prepare")}</button>}
            </div>)}
          </section>;
        })}
      </div>
      <footer>{t("course_archive.scope_note", { from, through })}</footer>
    </>}
  </main>;
}
