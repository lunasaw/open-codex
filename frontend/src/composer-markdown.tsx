import { useId, useRef, useState, type ClipboardEvent } from "react";
import { flushSync } from "react-dom";
import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { Eye, EyeOff } from "lucide-react";
import { clipboardMarkdown } from "./markdown-paste";
import { StaticMarkdown } from "./components/assistant-ui/elements/markdown-text";

export function MarkdownComposerInput(props: ComposerPrimitive.Input.Props) {
  const aui = useAui();
  const text = useAuiState(s => s.composer.text);
  const [preview, setPreview] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const previewId = useId();
  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    props.onPaste?.(event);
    if (event.defaultPrevented || event.currentTarget.disabled || event.currentTarget.readOnly) return;
    const markdown = clipboardMarkdown(event.clipboardData);
    if (markdown === null) return;
    event.preventDefault();
    const field = event.currentTarget;
    // Native insertion keeps undo/redo and the input's existing cursor handlers.
    if (document.execCommand("insertText", false, markdown)) return;
    const start = field.selectionStart;
    const end = field.selectionEnd;
    const value = field.value.slice(0, start) + markdown + field.value.slice(end);
    flushSync(() => aui.composer.setText(value));
    field.setSelectionRange(start + markdown.length, start + markdown.length);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  };
  return <div className="composer-markdown">
    <ComposerPrimitive.Input {...props} ref={input} onPaste={onPaste} />
    {text && <div className="composer-markdown-tools">
      <button type="button" aria-label={preview ? "收起 Markdown 预览" : "预览 Markdown"}
        aria-expanded={preview} aria-controls={previewId}
        onClick={() => { setPreview(!preview); input.current?.focus({ preventScroll: true }); }}>
        {preview ? <EyeOff size={14} /> : <Eye size={14} />}<span>{preview ? "收起预览" : "Markdown 预览"}</span>
      </button>
    </div>}
    {text && preview && <section id={previewId} aria-label="Markdown 预览" className="composer-markdown-preview">
      <StaticMarkdown text={text} />
    </section>}
  </div>;
}
