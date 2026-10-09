import { useEffect, useState } from "react";
export interface FocusTask { id: string; text: string }
interface FocusState { tasks: FocusTask[] }
export const FOCUS_STORAGE_KEY = "edison:focus:v1";
const empty = (): FocusState => ({ tasks: [] });
function parse(raw: string | null): FocusState | null {
  if (!raw) return empty();
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value.tasks) || !value.tasks.every((task: FocusTask) => task && typeof task.id === "string" && typeof task.text === "string" && task.text.trim())) return null;
    if (new Set(value.tasks.map((task: FocusTask) => task.id)).size !== value.tasks.length) return null;
    // Existing focused queues keep their current task when the old mode is removed.
    const tasks: FocusTask[] = value.tasks;
    const active = tasks.find(task => task.id === value.activeId);
    return { tasks: active ? [active, ...tasks.filter(task => task.id !== active.id)] : tasks };
  } catch { return null; }
}
export function useFocusQueue() {
  const [state, setState] = useState(() => { try { return parse(localStorage.getItem(FOCUS_STORAGE_KEY)) || empty(); } catch { return empty(); } });
  const [saveError, setSaveError] = useState(false);
  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key !== FOCUS_STORAGE_KEY) return;
      const next = parse(event.newValue);
      if (next) setState(next);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  function update(transform: (s: FocusState) => FocusState) {
    // Read the latest saved queue so another open tab's captures aren't overwritten.
    let latest = state;
    try { if (!saveError) latest = parse(localStorage.getItem(FOCUS_STORAGE_KEY)) || state; } catch { /* Use live state if storage is unavailable. */ }
    const next = transform(latest);
    try { localStorage.setItem(FOCUS_STORAGE_KEY, JSON.stringify(next)); setSaveError(false); } catch { setSaveError(true); }
    setState(next);
  }
  const current = state.tasks[0];
  return {
    ...state, current, saveError,
    add(text: string) { if (text.trim()) update(s => ({ tasks: [...s.tasks, { id: crypto.randomUUID(), text: text.trim() }] })); },
    edit(id: string, text: string) { if (text.trim()) update(s => ({ tasks: s.tasks.map(task => task.id === id ? { ...task, text: text.trim() } : task) })); },
    remove(id: string) { update(s => ({ tasks: s.tasks.filter(task => task.id !== id) })); },
    move(id: string, targetId: string) {
      update(s => {
        if (id === targetId) return s;
        const tasks = [...s.tasks];
        const from = tasks.findIndex(task => task.id === id), to = tasks.findIndex(task => task.id === targetId);
        if (from < 0 || to < 0) return s;
        tasks.splice(to, 0, tasks.splice(from, 1)[0]);
        return { tasks };
      });
    },
    complete() {
      // Complete the displayed task, even if another tab has just changed priority.
      if (current) update(s => ({ tasks: s.tasks.filter(task => task.id !== current.id) }));
    },
  };
}
export type FocusQueue = ReturnType<typeof useFocusQueue>;
