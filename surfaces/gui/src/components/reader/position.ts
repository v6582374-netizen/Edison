import type {ReaderPosition} from "../../api";
import type {FoliateView} from "./engine";

// Convert element endpoints into text endpoints before calling the pinned CFI
// encoder. Preserve UTF-16 offsets and exact whitespace, without DOM wrappers.
function textRange(range: Range, doc: Document): Range {
  const nodes: Node[] = [];
  const root = range.commonAncestorContainer;
  if (root.nodeType === 3) nodes.push(root);
  else {
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
  }
  let first: Range | undefined, last: Range | undefined;
  for (const node of nodes) {
    if (!range.intersectsNode(node)) continue;
    const part = doc.createRange(); part.selectNodeContents(node);
    if (range.compareBoundaryPoints(Range.START_TO_START, part) > 0) part.setStart(range.startContainer, range.startOffset);
    if (range.compareBoundaryPoints(Range.END_TO_END, part) < 0) part.setEnd(range.endContainer, range.endOffset);
    if (!part.toString()) continue;
    first ??= part; last = part;
  }
  if (!first || !last) throw new Error("This page has no text anchor");
  const normalized = doc.createRange();
  normalized.setStart(first.startContainer, first.startOffset);
  normalized.setEnd(last.endContainer, last.endOffset);
  if (normalized.startContainer.nodeType !== 3 || normalized.endContainer.nodeType !== 3 || normalized.toString() !== range.toString())
    throw new Error("Text anchor mismatch");
  return normalized;
}

export function capturePosition(view: FoliateView): ReaderPosition {
  const location = view.lastLocation;
  if (!location || !view.book) throw new Error("Reading location unavailable");
  const index = location.section.current;
  const content = view.renderer?.getContents().find(item => item.index === index);
  if (!content) throw new Error("Reading chapter unavailable");
  const {doc} = content;
  const section = view.book.sections[index].id;
  const start = location.range.startContainer;
  const tag = start.nodeType === 1 ? (start as Element).localName : "";
  if (!location.range.toString() && (tag === "img" || tag === "svg")) {
    const range = location.range.cloneRange(); range.collapse(true);
    const prefix = doc.createRange(); prefix.selectNodeContents(doc.body); prefix.setEnd(start, 0);
    const position: ReaderPosition = {kind: tag, cfi: view.getCFI(index, range), section, index,
      quote: "", offset: prefix.toString().length, length: doc.body.textContent!.length};
    verifyPosition(view, position, doc);
    return position;
  }
  // A textless cover has a chapter anchor, explicitly distinct from a text quote.
  if (!doc.body.textContent?.trim()) return {kind: "section", cfi: view.getCFI(index), section, index, quote: "", offset: 0, length: 0};
  const range = textRange(location.range, doc);
  let remaining = Math.min(48, range.toString().length), started = false;
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node === range.startContainer) started = true;
    if (!started) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.textContent!.length;
    if (remaining <= end - start) {range.setEnd(node, start + remaining); break;}
    remaining -= end - start;
  }
  // Avoid ending halfway through an emoji surrogate pair.
  if (/[\uD800-\uDBFF]$/.test(range.toString())) range.setEnd(range.endContainer, range.endOffset - 1);
  const prefix = doc.createRange(); prefix.selectNodeContents(doc.body); prefix.setEnd(range.startContainer, range.startOffset);
  const position: ReaderPosition = {kind: "text", cfi: view.getCFI(index, range), section, index, quote: range.toString(), offset: prefix.toString().length, length: doc.body.textContent!.length};
  verifyPosition(view, position, doc);
  return position;
}

export function verifyPosition(view: FoliateView, position: ReaderPosition, doc: Document) {
  const resolved = view.resolveCFI(position.cfi);
  if (resolved.index !== position.index || view.book?.sections[resolved.index]?.id !== position.section)
    throw new Error("Reading chapter mismatch");
  if (position.kind === "section") {
    if (doc.body.textContent?.trim()) throw new Error("Expected textless chapter");
    return;
  }
  const range = resolved.anchor(doc);
  const prefix = doc.createRange(); prefix.selectNodeContents(doc.body); prefix.setEnd(range.startContainer, range.startOffset);
  if (range.toString() !== position.quote || prefix.toString().length !== position.offset || doc.body.textContent?.length !== position.length)
    throw new Error("Saved reading text does not match this content version");
  if (position.kind !== "text" && (range.startContainer.nodeType !== 1 || (range.startContainer as Element).localName !== position.kind))
    throw new Error("Saved reading element does not match this content version");
}


// A highlight is the entire selection, not the short quote used for a stopping point.
export function captureHighlight(view: FoliateView, index: number, selection: Range): ReaderPosition {
  const doc = selection.startContainer.ownerDocument!;
  if (selection.collapsed || selection.endContainer.ownerDocument !== doc ||
      !doc.body.contains(selection.startContainer) || !doc.body.contains(selection.endContainer))
    throw new Error("Select text inside one chapter");
  const range = textRange(selection, doc);
  const quote = range.toString();
  if (!quote.trim() || quote.length > 12000) throw new Error("Select between 1 and 12000 text units");
  const prefix = doc.createRange(); prefix.selectNodeContents(doc.body); prefix.setEnd(range.startContainer, range.startOffset);
  const position: ReaderPosition = {kind: "text", cfi: view.getCFI(index, range), index,
    section: view.book!.sections[index].id, quote, offset: prefix.toString().length, length: doc.body.textContent!.length};
  verifyPosition(view, position, doc);
  return position;
}
