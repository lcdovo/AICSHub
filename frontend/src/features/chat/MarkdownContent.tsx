import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const plugins = [remarkGfm];

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={plugins}
      components={{
        table: (props) => (
          <div
            className="markdown-table"
            tabIndex={0}
            role="region"
            aria-label="回答中的表格"
          >
            <table {...props} />
          </div>
        ),
        a: (props) => (
          <a {...props} target="_blank" rel="noopener noreferrer" />
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
