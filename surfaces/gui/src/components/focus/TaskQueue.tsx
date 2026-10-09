import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { FocusQueue } from "./useFocusQueue";
import { Icon } from "../Icon";
export function TaskQueue({ queue }: { queue: FocusQueue }) {
  const { t } = useTranslation();
  const [dragging, setDragging] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
  const list = useRef<HTMLOListElement>(null);
  const drag = useRef<{ id: string; x: number; y: number; moved: boolean; target: string | null } | null>(null);
  function resetDrag() { drag.current = null; setDragging(null); setTarget(null); }
  return <section className="fm-queue" aria-label={t("focus.queue")}><div className="fm-queue-heading"><h2>{t("focus.queue")}</h2><span>{queue.tasks.length}</span></div><p className="fm-queue-hint">{t("focus.drag_hint")}</p>
    <ol ref={list}>{queue.tasks.map((task, index) => <li key={task.id} data-task-id={task.id} className={(dragging === task.id ? "is-dragging " : "") + (target === task.id && target !== dragging ? "is-drop-target" + (index > queue.tasks.findIndex(item => item.id === dragging) ? " drop-after" : "") : "")}>
      <button className="fm-drag" aria-label={t("focus.reorder_task", { task: task.text })} title={t("focus.keyboard_hint")} onPointerDown={e => {
        if (e.button !== 0) return;
        drag.current = { id: task.id, x: e.clientX, y: e.clientY, moved: false, target: null };
        e.currentTarget.setPointerCapture(e.pointerId);
      }} onPointerMove={e => {
        const d = drag.current;
        if (!d || d.id !== task.id) return;
        if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 6) return;
        d.moved = true; setDragging(d.id);
        const row = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-task-id]");
        d.target = row && list.current?.contains(row) ? row.dataset.taskId || null : null;
        setTarget(d.target);
        const bounds = list.current!.getBoundingClientRect();
        if (e.clientY > bounds.bottom - 32) list.current!.scrollTop += 12;
        else if (e.clientY < bounds.top + 32) list.current!.scrollTop -= 12;
      }} onPointerUp={e => {
        const d = drag.current;
        if (d?.moved && d.target) queue.move(d.id, d.target);
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        resetDrag();
      }} onPointerCancel={resetDrag} onLostPointerCapture={resetDrag} onKeyDown={e => {
        if (e.key === "Escape" && drag.current) { e.preventDefault(); resetDrag(); return; }
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        const next = queue.tasks[index + (e.key === "ArrowUp" ? -1 : 1)];
        if (next) queue.move(task.id, next.id);
      }}><svg width="12" height="18" viewBox="0 0 12 18" fill="currentColor" aria-hidden="true">{[4,9,14].flatMap(y => [3,9].map(x => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.2" />))}</svg></button>
      <span className="fm-queue-number">{String(index + 1).padStart(2, "0")}</span>
      {editing === task.id ? <form className="fm-edit" onSubmit={e => {e.preventDefault(); if (text.trim()) {queue.edit(task.id, text); setEditing(null);}}}><input autoFocus aria-label={t("focus.edit_task")} value={text} maxLength={500} onChange={e => setText(e.target.value)} onKeyDown={e => {if (e.key === "Escape") { e.preventDefault(); setEditing(null); }}} /><button type="submit" disabled={!text.trim()} aria-label={t("focus.save")}><span aria-hidden="true">✓</span></button></form> : <button className="fm-queue-text" onClick={() => {setText(task.text); setEditing(task.id);}} aria-label={t("focus.edit_named_task", {task: task.text})}>{task.text}</button>}
      <button className="fm-remove" aria-label={t("focus.remove_task", {task: task.text})} onClick={() => queue.remove(task.id)}><Icon name="x" size={14} /></button>
    </li>)}</ol>
    {queue.tasks.length === 0 && <p className="fm-queue-empty">{t("focus.queue_empty")}</p>}
  </section>;
}
