import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import ClassroomMarkdown from "./ClassroomMarkdown";

test("renders equations without changing code or fetching external illustrations", () => {
  const { container } = render(<ClassroomMarkdown text={'# 公式\n\n$$a^2+b^2=c^2$$\n\n```python\nprice = "$20"\n```\n\n![outside](https://example.com/track.png)'} />);
  expect(container.querySelector(".katex math")).not.toBeNull();
  expect(container.querySelector("pre")?.textContent).toContain('price = "$20"');
  expect(container.querySelector("img")).toBeNull();
});

test("reads LaTeX display and inline formulas while preserving literal code", () => {
  const { container } = render(<ClassroomMarkdown text={'\\[a^2+b^2=c^2\\]\n\nInline \\(x+1\\).\n\n```python\npattern = r"\\(x\\)"\n```\n\n`\\[literal\\]`'} />);
  expect(container.querySelectorAll(".katex")).toHaveLength(2);
  expect(container.querySelector("pre")?.textContent).toContain('pattern = r"\\(x\\)"');
  expect(container.textContent).toContain('\\[literal\\]');
});

test("closes indented and mixed-line display math before the next paragraph", () => {
  const { container } = render(<ClassroomMarkdown text={String.raw`1. 结论
   \[
   |kA|=k^n|A|
   \]

   而不是 \(k|A|\)。

$$a+b
=c$$

## 后续正文

仍是普通讲解。`} />);
  expect(container.querySelectorAll(".katex math")).toHaveLength(3);
  expect(container.querySelector(".katex-error")).toBeNull();
  expect(container.querySelector("h2")?.textContent).toBe("后续正文");
  expect(container.querySelector("li p:last-child")?.textContent).toContain("而不是");
});
