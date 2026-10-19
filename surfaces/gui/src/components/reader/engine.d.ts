export interface TocEntry { label: string; href: string; subitems?: TocEntry[] }
export interface ReaderBookEngine {
  metadata: { title?: string }; toc?: TocEntry[]; contentRule: string; destroy(): void;
  sections: {id: string; createDocument(): Promise<Document>}[];
}
export interface FoliateView extends HTMLElement {
  book?: ReaderBookEngine;
  renderer?: HTMLElement & {setStyles?(css: string): void; getContents(): {doc: Document; index: number; overlayer?: {add(key: string, range: Range, draw: unknown): void; remove(key: string): void}}[]};
  lastLocation?: {range: Range; cfi: string; section: {current: number}};
  getCFI(index: number, range?: Range): string;
  resolveCFI(cfi: string): {index: number; anchor(doc: Document): Range};
  addAnnotation(annotation: {value: string; color?: string}): Promise<unknown>;
  deleteAnnotation(annotation: {value: string}): Promise<unknown>;
  open(book: ReaderBookEngine): Promise<void>;
  init(options: {showTextStart?: boolean; lastLocation?: string}): Promise<void>;
  goTo(href: string): Promise<unknown>; prev(): Promise<void>; next(): Promise<void>; close(): void;
}
