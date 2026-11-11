import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { EditorState, Prec } from "@codemirror/state";
import {
  Decoration,
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  MatchDecorator,
  placeholder as placeholderExt,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";
import { useEffect, useRef } from "react";

/** Marks every {{input}} so it stands out as the place the test input goes. */
const inputMatcher = new MatchDecorator({
  regexp: /\{\{input\}\}/g,
  decoration: Decoration.mark({ class: "cm-input-token" }),
});
const inputHighlighter = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = inputMatcher.createDeco(view);
    }
    update(update: ViewUpdate) {
      this.decorations = inputMatcher.updateDeco(update, this.decorations);
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

// Colours come from the site's CSS variables, so the editor follows the light/dark theme.
const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "13.5px", backgroundColor: "var(--surface-2)", color: "var(--text)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.6", overflow: "auto" },
  ".cm-content": { padding: "10px 0", caretColor: "var(--accent)" },
  ".cm-line": { padding: "0 12px 0 8px" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", border: "none", borderRight: "1px solid var(--border)" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 10px", minWidth: "28px", opacity: "0.7" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--text) 4%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text)", opacity: "1" },
  ".cm-cursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "color-mix(in srgb, var(--accent) 28%, transparent)",
  },
  ".cm-placeholder": { color: "var(--muted)" },
});

interface Props {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  onSubmit: () => void;
  placeholder?: string;
}

/** The prompt editor: line numbers, soft wrap, undo/redo and {{input}} highlighting. */
export default function PromptEditor({ value, onChange, onRun, onSubmit, placeholder }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView>();
  // Latest callbacks, so the editor is created once and never rebuilt on re-render.
  const handlers = useRef({ onChange, onRun, onSubmit });
  handlers.current = { onChange, onRun, onSubmit };

  useEffect(() => {
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          // Before the default keymap, where Ctrl/Cmd+Enter would insert a blank line.
          Prec.highest(
            keymap.of([
              { key: "Mod-Enter", run: () => (handlers.current.onRun(), true) },
              { key: "Shift-Mod-Enter", run: () => (handlers.current.onSubmit(), true) },
            ]),
          ),
          lineNumbers(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          drawSelection(),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorView.lineWrapping,
          EditorState.tabSize.of(2),
          inputHighlighter,
          placeholderExt(placeholder ?? ""),
          EditorView.contentAttributes.of({ "aria-label": "Your prompt", spellcheck: "false" }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) handlers.current.onChange(update.state.doc.toString());
          }),
          theme,
        ],
      }),
    });
    view.current = editor;
    return () => editor.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Outside changes (Reset, loading a submission, restoring history) replace the document.
  useEffect(() => {
    const editor = view.current;
    if (editor && value !== editor.state.doc.toString()) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div className="editor-host" ref={host} />;
}
