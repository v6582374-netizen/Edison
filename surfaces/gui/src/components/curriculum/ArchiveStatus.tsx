import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { CourseArchiveSnapshot } from "../../api";
import { Icon } from "../Icon";

/** Native disclosure keeps keyboard navigation and focus in the browser's hands. */
export function ArchiveStatus({ snapshot }: { snapshot: CourseArchiveSnapshot }) {
  const { t, i18n } = useTranslation();
  const disclosure = useRef<HTMLDetailsElement>(null);
  const backup = snapshot.backup?.state === "synced" ? "synced" : snapshot.backup?.state === "conflict" ? "conflict" : snapshot.backup?.state === "pending" ? "pending" : "unknown";
  const enabled = snapshot.automation?.enabled === true;
  const checkingLabel = !snapshot.automation ? "course_archive.checking_unknown" : enabled ? "course_archive.checking_enabled" : "course_archive.checking_paused";
  const errors = Object.values(snapshot.automation?.errors ?? {});
  if (snapshot.automation?.generation_error) errors.push(snapshot.automation.generation_error);
  const formatTime = (value?: string) => value ? new Intl.DateTimeFormat(i18n.language, {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(new Date(value)) : t("course_archive.no_confirmation");

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (disclosure.current && !disclosure.current.contains(event.target as Node)) disclosure.current.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  return <div className="course-archive-statusbar">
    <span className="course-archive-auto"><Icon name={enabled ? "refresh" : "pause"} size={14} />{t(checkingLabel)}</span>
    <details ref={disclosure} className="course-archive-status-disclosure" data-backup={backup}
      onPointerDown={() => { if (disclosure.current) disclosure.current.dataset.motion = "pointer"; }}
      onKeyDown={event => {
        if (disclosure.current) disclosure.current.dataset.motion = "keyboard";
        if (event.key === "Escape" && disclosure.current?.open) {
          disclosure.current.open = false;
          disclosure.current.querySelector("summary")?.focus();
          event.preventDefault();
        }
      }}>
      <summary>
        <span className="course-archive-backup-icon" aria-hidden="true">
          <span data-active={backup === "synced"}><Icon name="shield" size={15} /></span>
          <span data-active={backup === "pending" || backup === "unknown"}><Icon name="clock" size={15} /></span>
          <span data-active={backup === "conflict"}><Icon name="warning" size={15} /></span>
        </span>
        <span role="status">{t(`course_archive.backup_label_${backup}`)}</span>
        <Icon name="chevronDown" size={13} />
      </summary>
      <div className="course-archive-status-panel">
        <section><h2>{t(checkingLabel)}</h2>
          <p>{t("course_archive.last_check")}</p><time dateTime={snapshot.automation?.checked_at}>{formatTime(snapshot.automation?.checked_at)}</time>
        </section>
        <section><h2>{t(`course_archive.backup_detail_${backup}`)}</h2>
          <p>{t("course_archive.last_backup")}</p><time dateTime={snapshot.backup?.checked_at}>{formatTime(snapshot.backup?.checked_at)}</time>
          <p>{t(`course_archive.backup_note_${backup}`)}</p>
          {!!snapshot.backup?.conflicts?.length && <p>{t("course_archive.conflicts", { count: snapshot.backup.conflicts.length })}</p>}
        </section>
        {snapshot.automation?.preparing && <p>{t("course_archive.preparing_count", { count: snapshot.automation.pending ?? 0 })}</p>}
        {[...new Set(errors)].map(message => <p className="course-archive-status-error" key={message}>{message}</p>)}
        <p className="course-archive-timezone">{t("course_archive.beijing_time")}</p>
      </div>
    </details>
  </div>;
}
