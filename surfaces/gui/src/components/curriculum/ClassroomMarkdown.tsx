import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

export function mathDelimiters(text: string) {
  // Keep code literal; preserve list/quote indentation on both display fences.
  return text.replace(/(`+|~{3,})([\s\S]*?)\1|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|\\\(([\s\S]*?)\\\)/g,
    (match, code, _body, latexDisplay, dollarDisplay, inline, offset) => {
      if (code) return match;
      const display = latexDisplay ?? dollarDisplay;
      if (display === undefined) return `$${inline}$`;
      const prefix = text.slice(text.lastIndexOf("\n", offset - 1) + 1, offset);
      const indent = /^[\t >]*$/.test(prefix) ? prefix : "";
      return `$$\n${indent}${display.trim()}\n${indent}$$`;
    });
}

// Classroom notes need readable equations as well as code and tables.
export default function ClassroomMarkdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown
    remarkPlugins={[remarkGfm, remarkMath]}
    rehypePlugins={[[rehypeKatex, { strict: false, trust: false }]]}
    skipHtml
    components={{
      a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
      img: () => null,
    }}
  >{mathDelimiters(text)}</ReactMarkdown></div>;
}
