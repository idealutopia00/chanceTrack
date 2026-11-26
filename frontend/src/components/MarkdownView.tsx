import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownViewProps {
  markdown: string;
  dialogTitle?: string;
}

export function MarkdownView({ markdown, dialogTitle }: MarkdownViewProps) {
  const trimmed = markdown.trimStart();
  const firstLine = trimmed.split(/\r?\n/, 1)[0]?.trim() ?? "";
  const hideFirstH1 =
    Boolean(dialogTitle) && firstLine === `# ${dialogTitle?.trim()}`;

  return (
    <div className={`ct-prose ${hideFirstH1 ? "hide-first-h1" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children, ...props }) => (
            <div className="ct-table-wrap" tabIndex={0} role="region" aria-label="备忘录表格">
              <table {...props}>{children}</table>
            </div>
          ),
          a: ({ children, href, ...props }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          ),
          img: () => null,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
