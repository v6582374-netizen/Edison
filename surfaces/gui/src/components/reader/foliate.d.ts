declare module "foliate-js/view.js";

declare module "foliate-js/overlayer.js" {
  export class Overlayer {static highlight(rects: DOMRectList, options?: {color: string}): SVGElement; static outline(rects: DOMRectList): SVGElement}
}
declare module "foliate-js/text-walker.js" {export const textWalker: unknown;}
declare module "foliate-js/search.js" {
  export function searchMatcher(walker: unknown, options: {matchCase: boolean; matchDiacritics: boolean}):
    (doc: Document, query: string) => Generator<{range: Range; excerpt: {pre: string; match: string; post: string}}>;
}
