import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "./Icon";
import { addDays, curriculumWeek, MAX_DATE, MIN_DATE, minutes, PERIODS, shanghaiToday, validDate } from "./curriculum/timetable";
import type { Occurrence } from "./curriculum/timetable";
import { NotificationControl } from "./curriculum/NotificationControl";
import { CourseDetails } from "./curriculum/CourseDetails";
import "./curriculum/curriculum.css";

const GRID_START = 8 * 60;
const GRID_END = 21 * 60;
const position = (time: string) => `${(minutes(time) - GRID_START) / (GRID_END - GRID_START) * 100}%`;

export function CurriculumView({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const [today, setToday] = useState(shanghaiToday);
  const [selectedDate, setSelectedDate] = useState(shanghaiToday);
  const [selectedCourse, setSelectedCourse] = useState<Occurrence | null>(null);
  const schedule = curriculumWeek(selectedDate);
  const hasTimetable = schedule.days.some(day => day.covered);
  const locale = i18n.language.startsWith("zh") ? "zh-CN" : "en-GB";
  const formatDate = (date: string, options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" }).format(new Date(`${date}T00:00:00Z`));

  useEffect(() => {
    let previousToday = shanghaiToday();
    const update = () => {
      const next = shanghaiToday();
      if (next !== previousToday) {
        const previous = previousToday;
        setToday(next);
        setSelectedDate(selected => selected === previous ? next : selected);
        previousToday = next;
      }
    };
    const timer = window.setInterval(update, 60_000);
    window.addEventListener("focus", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); };
  }, []);

  return (
    <main className="curriculum" aria-label="Curriculum">
      <header className="curriculum-header">
        <div>
          <p className="curriculum-eyebrow">{t("curriculum.university")} <span>2026 — 2027</span></p>
          <h1>Curriculum</h1>
          <p className="curriculum-subtitle">{t("curriculum.subtitle")}</p>
        </div>
        <div className="curriculum-header-actions">
          <NotificationControl />
          <span className="curriculum-semester">{t(`curriculum.${schedule.semester ?? "outside_term"}`)}</span>
        </div>
      </header>
      <section className="curriculum-toolbar" aria-label={t("curriculum.navigation")}>
        <div className="curriculum-week-title" aria-live="polite">
          <h2>{schedule.week ? t("curriculum.week", { week: schedule.week }) : t("curriculum.week_view")}</h2>
          <span>{formatDate(schedule.monday, { year: "numeric", month: "short", day: "numeric" })} — {formatDate(addDays(schedule.monday, 6), { month: "short", day: "numeric" })}</span>
        </div>
        <div className="curriculum-controls">
          <button className="curriculum-control" onClick={() => setSelectedDate(today)}>{t("curriculum.this_week")}</button>
          <div className="curriculum-arrows">
            <button aria-label={t("curriculum.previous_week")} disabled={!validDate(addDays(selectedDate, -7))} onClick={() => setSelectedDate(addDays(selectedDate, -7))}><Icon name="chevronRight" className="curriculum-back" /></button>
            <button aria-label={t("curriculum.next_week")} disabled={!validDate(addDays(selectedDate, 7))} onClick={() => setSelectedDate(addDays(selectedDate, 7))}><Icon name="chevronRight" /></button>
          </div>
          <input className="curriculum-date" type="date" aria-label={t("curriculum.jump_date")} min={MIN_DATE} max={MAX_DATE} value={selectedDate} onChange={event => { if (validDate(event.target.value)) setSelectedDate(event.target.value); }} />
        </div>
      </section>
      {schedule.notes.length > 0 && <div className="curriculum-notes">
        {schedule.notes.map(event => <span key={`${event.start}-${event.key}`}>
          <Icon name="clock" size={13} />
          {formatDate(event.start, { month: "numeric", day: "numeric" })} · {t(`curriculum.events.${event.note}`)}
        </span>)}
      </div>}
      <div className="curriculum-summary">
        <span>{schedule.semester === "autumn" ? t("curriculum.sessions", { count: schedule.count }) : t("curriculum.calendar_only")}</span>
        <span>{t("curriculum.timezone")}</span>
      </div>
      <div className="curriculum-scroll" tabIndex={0} role="region" aria-label={t("curriculum.weekly_timetable")}>
        <div className="curriculum-board">
          <div className="curriculum-day-headings">
            <div className="curriculum-time-heading">{t("curriculum.time")}</div>
            {schedule.days.map(day => <div key={day.date} className={`curriculum-day-heading ${day.date === today ? "is-today" : ""}`}>
              <div className="curriculum-day-label">
                <span>{formatDate(day.date, { weekday: "short" })}</span>
                <time dateTime={day.date} aria-current={day.date === today ? "date" : undefined}>{formatDate(day.date, { month: "numeric", day: "numeric" })}</time>
              </div>
              <div className="curriculum-day-events">{day.events.map(event => <span key={`${event.start}-${event.key}`}>{t(`curriculum.events.${event.key}`)}</span>)}</div>
            </div>)}
          </div>
          {hasTimetable && <div className="curriculum-grid">
            <div className="curriculum-axis" aria-hidden="true">
              {PERIODS.map(([start], i) => <span key={start} style={{ top: position(start) }}><b>{start}</b><small>{i + 1}</small></span>)}
            </div>
            {schedule.days.map(day => <section key={day.date} className={`curriculum-day ${day.date === today ? "is-today" : ""}`} aria-label={day.date}>
              {PERIODS.map(([start]) => <div key={start} className="curriculum-period-line" style={{ top: position(start) }} />)}
              {day.courses.map(course => <button key={course.id} className="curriculum-course" onClick={() => setSelectedCourse(course)} style={{ top: position(course.start), height: `${(minutes(course.end) - minutes(course.start)) / (GRID_END - GRID_START) * 100}%`, "--course-color": course.color, "--lane": course.lane, "--lanes": course.lanes } as CSSProperties}>
                <span className="curriculum-course-time">{course.start}–{course.end}</span>
                <strong>{course.name}</strong>
                <span className="curriculum-course-room">{course.room}</span>
              </button>)}
              {!day.courses.length && day.covered && <span className="curriculum-free">{t("curriculum.no_courses")}</span>}
              {!day.covered && <span className="curriculum-free">{t("curriculum.no_timetable")}</span>}
            </section>)}
          </div>}
        </div>
        {!hasTimetable && <div className="curriculum-empty" role="status">
          <Icon name="book" size={26} />
          <h3>{t(`curriculum.${schedule.semester === "spring" ? "spring_missing" : "timetable_missing"}`)}</h3>
          <p>{t("curriculum.missing_explanation")}</p>
        </div>}
      </div>
      {selectedCourse && <CourseDetails course={selectedCourse} onClose={() => setSelectedCourse(null)} onOpenDocument={onOpenDocument} />}
      <footer className="curriculum-footer">{t("curriculum.source")}</footer>
    </main>
  );
}
