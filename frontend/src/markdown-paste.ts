import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

const converter = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
});
converter.use(gfm);
converter.remove(["script", "style", "noscript", "iframe", "object", "template"]);
converter.addRule("strikethrough", {
  filter: node => ["DEL", "S", "STRIKE"].includes(node.nodeName),
  replacement: content => `~~${content}~~`,
});
converter.addRule("tableCell", {
  filter: ["th", "td"],
  replacement: (content, node) =>
    `${node.previousSibling ? " " : "| "}${content.trim().replace(/\|/g, "\\|").replace(/\n+/g, "<br>")} |`,
});

/** Return null to leave plain text and file pastes to the native input. */
export function clipboardMarkdown(data: Pick<DataTransfer, "getData" | "files">): string | null {
  if (data.files.length) return null;
  const markdown = data.getData("text/markdown");
  if (markdown) return markdown;
  const html = data.getData("text/html");
  if (!html) return null;

  const plain = data.getData("text/plain");
  try {
    // A template remains inert: pasted scripts and remote images are never mounted.
    const template = document.createElement("template");
    template.innerHTML = html;
    const fragment = template.content;
    fragment.querySelectorAll("script,style,noscript,iframe,object,template,head,meta,link").forEach(node => node.remove());
    // Editors often supply HTML syntax highlighting alongside literal Markdown.
    if (plain.trim() === fragment.textContent?.trim() &&
      /(^|\n)\s{0,3}(?:#{1,6}\s|`{3,}|~{3,}|[-*+]\s|\d+\.\s|>\s|\|)|`[^`\n]+`|\*{1,2}[^*\n]+\*{1,2}|_{1,2}[^_\n]+_{1,2}|~~[^~\n]+~~|\[[^\]]+\]\([^\n]+\)/.test(plain)) return null;
    if (plain && !fragment.querySelector("h1,h2,h3,h4,h5,h6,p,div,br,strong,b,em,i,ul,ol,li,blockquote,pre,code,table,a,del,s,strike")) return null;
    // GFM needs a header row, including tables copied from spreadsheets.
    fragment.querySelectorAll("table").forEach(table => {
      table.querySelectorAll("thead,tbody,tfoot,tr").forEach(section => {
        for (const node of Array.from(section.childNodes)) {
          if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim()) node.remove();
        }
      });
      const firstRow = table.rows[0];
      if (!firstRow) { table.remove(); return; }
      for (const cell of Array.from(firstRow.cells)) {
        if (cell.tagName === "TH") continue;
        const heading = document.createElement("th");
        heading.append(...cell.childNodes);
        cell.replaceWith(heading);
      }
    });
    return converter.turndown(fragment) || null;
  } catch {
    // Keep the original plain text available if unusual HTML cannot be converted.
    return null;
  }
}
