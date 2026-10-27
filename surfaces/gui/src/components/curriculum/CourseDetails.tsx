import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../Icon";
import { minutes, PERIODS, type Occurrence } from "./timetable";
import { getOccurrenceDocuments } from "../../api";
import type { RecordedClass } from "../../api";
import "./archive.css";

// Personal additions belong to the course, not one occurrence, and stay on this device.
type Entry = { id: string; label: string; value: string };
const STORAGE_KEY = "edison.curriculum.entries";

function readAll(): Record<string, Entry[]> {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") ?? {}; } catch { return {}; }
}

function writeCourse(course: string, entries: Entry[]) {
  const all = readAll();
  const kept = entries.filter(entry => entry.label.trim() || entry.value.trim());
  if (kept.length) all[course] = kept; else delete all[course];
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(all)); } catch { /* best effort */ }
}

export function CourseDetails({ course, onClose, onOpenDocument }: { course: Occurrence; onClose: () => void; onOpenDocument?: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const [entries, setEntries] = useState<Entry[]>(() => readAll()[course.course] ?? []);
  const [fresh, setFresh] = useState<string | null>(null);
  const [archive, setArchive] = useState<{ state: string; recordings: RecordedClass[] } | null>(null);
  useEffect(() => {
    let live = true;
    getOccurrenceDocuments(course.course, course.date, course.first, course.last)
      .then(value => { if (live) setArchive(value); })
      .catch(() => { if (live) setArchive({ state: "error", recordings: [] }); });
    return () => { live = false; };
  }, [course.id, course.course, course.date, course.first, course.last]);
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); previous?.focus(); };
  }, []);
  useEffect(() => { writeCourse(course.course, entries); }, [course.course, entries]);
  const date = new Intl.DateTimeFormat(i18n.language.startsWith("zh") ? "zh-CN" : "en-GB", {
    timeZone: "UTC", year: "numeric", month: "long", day: "numeric", weekday: "long",
  }).format(new Date(`${course.date}T00:00:00Z`));
  const update = (id: string, patch: Partial<Entry>) => setEntries(list => list.map(entry => entry.id === id ? { ...entry, ...patch } : entry));
  const add = () => {
    const id = crypto.randomUUID();
    setEntries(list => [...list, { id, label: "", value: "" }]);
    setFresh(id);
  };

  return (
    <dialog ref={ref} className="curriculum-details" aria-labelledby="curriculum-course-title" onCancel={event => { event.preventDefault(); onClose(); }}>
      <header>
        <span>{t("curriculum.course_details")}</span>
        <button type="button" aria-label={t("curriculum.close")} onClick={onClose}><Icon name="x" size={18} /></button>
      </header>
      <h2 id="curriculum-course-title">{course.name}</h2>
      <p className="curriculum-details-date">{date}</p>
      <dl>
        <dt>{t("curriculum.class_time")}</dt><dd>{course.start}–{course.end}<small>{t("curriculum.period_range", { first: course.first, last: course.last })}</small>
          <ol className="curriculum-details-periods" aria-label={t("curriculum.period_times")}>
            {PERIODS.slice(course.first - 1, course.last).map(([start, end], index) => <li key={start}>
              <span>{t("curriculum.period", { number: course.first + index })}<time>{start}–{end}</time></span>
              {course.first + index < course.last && <small>{t("curriculum.break_minutes", { count: minutes(PERIODS[course.first + index][0]) - minutes(end) })} · {end}–{PERIODS[course.first + index][0]}</small>}
            </li>)}
          </ol>
        </dd>
        <dt>{t("curriculum.classroom")}</dt><dd>{course.room}</dd>
        <dt>{t("curriculum.teacher")}</dt><dd>{course.teacher}</dd>
        <dt>{t("curriculum.assessment")}</dt>
        <dd><span className={`curriculum-assessment is-${course.assessment}`}>{t(`curriculum.assessments.${course.assessment}`)}</span>
          <small>{t(`curriculum.categories.${course.category}`)} · {t("curriculum.credits", { count: course.credits })}</small></dd>
        {entries.map(entry => <div key={entry.id} className="curriculum-entry">
          <dt><input aria-label={t("curriculum.entry_label")} placeholder={t("curriculum.entry_label")} value={entry.label}
            autoFocus={entry.id === fresh} onChange={event => update(entry.id, { label: event.target.value })} /></dt>
          <dd><textarea aria-label={t("curriculum.entry_value")} placeholder={t("curriculum.entry_value")} value={entry.value}
            rows={Math.max(1, entry.value.split("\n").length)}
            onChange={event => update(entry.id, { value: event.target.value })} />
            <button type="button" aria-label={t("curriculum.remove_entry")} onClick={() => setEntries(list => list.filter(item => item.id !== entry.id))}><Icon name="x" size={14} /></button></dd>
        </div>)}
      </dl>
      <section className="curriculum-archive-information" aria-label={t("course_archive.information")}>
        <h3>{t("course_archive.information")}</h3>
        {!archive && <p role="status">{t("course_archive.loading")}</p>}
        {archive && archive.state !== "matched" && <p>{t(`course_archive.${archive.state}`)}</p>}
        {archive?.recordings.map(record => <div key={record.id}>
          {record.documents.length ? record.documents.map(doc => <button key={doc.id} onClick={() => { onClose(); onOpenDocument?.(doc.id); }}>{doc.title} ↗</button>) : <p>{record.title} · {t(`course_archive.states.${record.state}`, { defaultValue: record.state })}{record.detail && ` · ${record.detail}`}</p>}
        </div>)}
      </section>
      <button type="button" className="curriculum-add-entry" onClick={add}>+ {t("curriculum.add_entry")}</button>
      {course.course === "tourism" && <p className="curriculum-details-note">{t("curriculum.english_instruction")}</p>}
      <footer>{t("curriculum.university")} · {t("curriculum.timezone")}</footer>
    </dialog>
  );
}
