import {saveReaderReading, type ReaderPosition, type ReaderPreferences, type ReaderReading, type ReaderReadingUpdate} from "../../api";

// One ordered stream per open book. A failed response retains the exact operation
// for idempotent retry; subsequent local edits never reuse its revision or ID.
export class ReadingWriter {
  record: ReaderReading;
  error: unknown = null;
  dirty = false;
  private draft: {position: ReaderPosition | null; furthest: ReaderPosition | null; preferences: ReaderPreferences};
  private attempt: ReaderReadingUpdate | null = null;
  private running: Promise<boolean> | null = null;
  constructor(private bookId: string, record: ReaderReading, private changed: () => void) {
    this.record = record;
    this.draft = {position: record.position, furthest: record.furthest, preferences: record.preferences};
  }
  update(position: ReaderPosition | null, preferences: ReaderPreferences) {
    let furthest = this.draft.furthest;
    if (position && (!furthest || position.index > furthest.index || (position.index === furthest.index && position.offset > furthest.offset))) furthest = position;
    const next = {position, furthest, preferences};
    if (JSON.stringify(this.draft) === JSON.stringify(next)) return;
    this.draft = next; this.dirty = true; this.changed();
    void this.flush();
  }
  async retry() {this.error = null; this.changed(); return this.flush();}
  flush(): Promise<boolean> {
    if (this.error) return Promise.resolve(false);
    if (this.running) return this.running;
    if (!this.dirty) return Promise.resolve(true);
    this.running = this.write().finally(() => {this.running = null; if (this.dirty && !this.error) void this.flush();});
    return this.running;
  }
  private async write(): Promise<boolean> {
    while (this.dirty && !this.error) {
      const attempt = this.attempt ?? {
        ...this.draft, content_version: this.record.content_version,
        revision: this.record.revision, operation_id: crypto.randomUUID(),
      };
      this.attempt = attempt;
      try {
        this.record = await saveReaderReading(this.bookId, attempt);
        this.attempt = null;
        this.dirty = JSON.stringify(this.draft) !== JSON.stringify({position: attempt.position, furthest: attempt.furthest, preferences: attempt.preferences});
      } catch (error) {this.error = error;}
      this.changed();
    }
    return !this.dirty;
  }
}
