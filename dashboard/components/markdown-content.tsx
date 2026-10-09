"use client";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

const INTERNAL_ORIGIN = "https://observatory.invalid";

function safeMarkdownLink(href: string) {
  const value = href.trim();
  if (!value || /[\u0000-\u001f]/.test(value) || value.startsWith("\\") || value.startsWith("//")) return null;
  try {
    const url = new URL(value, INTERNAL_ORIGIN);
    if (url.protocol === "file:") {
      if (url.hostname && url.hostname !== "localhost") return null;
      return { kind: "file" as const, href: value };
    }
    if (url.origin === INTERNAL_ORIGIN) {
      return { kind: "internal" as const, href: `${url.pathname}${url.search}${url.hash}` };
    }
    if (["http:", "https:", "mailto:", "tel:"].includes(url.protocol)) {
      return { kind: "external" as const, href: value };
    }
  } catch {
    return null;
  }
  return null;
}

function workspaceFilePath(href: string) {
  try {
    let path: string;
    if (href.startsWith("/workspace/")) {
      path = decodeURIComponent(href.split(/[?#]/, 1)[0].slice("/workspace/".length));
    } else if (!href.startsWith("/") && !href.startsWith("#") && !href.startsWith("?") && !/^[a-z][a-z0-9+.-]*:/i.test(href)) {
      path = decodeURIComponent(href.split(/[?#]/, 1)[0]).replace(/^\.\//, "");
    } else {
      const url = new URL(href);
      if (url.protocol !== "file:" || url.hostname || !url.pathname.startsWith("/workspace/")) return null;
      path = decodeURIComponent(url.pathname.slice("/workspace/".length));
    }
    const normalized = path.replaceAll("\\", "/");
    const segments = normalized.split("/");
    if (!normalized || normalized.startsWith("/") || segments.some(segment => segment === ".." || segment.includes(":") || segment.includes("\0"))) return null;
    return segments.filter(segment => segment && segment !== ".").join("/");
  } catch {
    return null;
  }
}

export function MarkdownContent({ children, className = "", onOpenFile }: {
  children: string; className?: string; onOpenFile?: (path: string) => void;
}) {
  const components: Components = {
    a({ href, children, title }) {
      const link = href ? safeMarkdownLink(href) : null;
      if (!link) return <span>{children}</span>;
      const path = href ? workspaceFilePath(href) : null;
      if (path) return onOpenFile
        ? <button type="button" className="markdown-file-link" title={`Open workspace file ${path}`} onClick={() => onOpenFile(path)}>{children}</button>
        : <span title="Local file links are available from an agent workspace snapshot">{children} <small>(workspace file)</small></span>;
      if (link.kind === "internal") return <Link href={link.href} title={title}>{children}</Link>;
      if (link.kind === "file") {
        return <span title="Only files in the shared workspace snapshot can be opened here">{children} <small>(local file)</small></span>;
      }
      return <a href={link.href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
    table({ children }) {
      return <div className="markdown-table-scroll"><table>{children}</table></div>;
    },
    img() {
      return <span className="muted">[Image omitted]</span>;
    },
  };
  return <div className={`markdown-content ${className}`.trim()}>
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      components={components} urlTransform={url => safeMarkdownLink(url)?.href || ""}>
      {children}
    </ReactMarkdown>
  </div>;
}
