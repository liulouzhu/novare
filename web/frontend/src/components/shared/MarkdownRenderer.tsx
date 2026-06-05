/** Markdown + LaTeX + 代码高亮渲染器 */

import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import remarkGfm from 'remark-gfm'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

interface Props {
  content: string
}

export function MarkdownRenderer({ content }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, remarkGfm]}
      rehypePlugins={[rehypeKatex, rehypeHighlight]}
      components={{
        // 代码块：添加复制按钮
        pre: ({ children, ...props }) => (
          <div className="relative group my-3">
            <pre {...props} className="overflow-x-auto rounded-lg text-sm">
              {children}
            </pre>
            <CopyButton />
          </div>
        ),
        // 表格样式
        table: ({ children, ...props }) => (
          <div className="overflow-x-auto my-3">
            <table {...props} className="min-w-full border-collapse text-sm">
              {children}
            </table>
          </div>
        ),
        th: ({ children, ...props }) => (
          <th
            {...props}
            className="px-3 py-2 text-left text-xs font-medium border"
            style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-tertiary)' }}
          >
            {children}
          </th>
        ),
        td: ({ children, ...props }) => (
          <td
            {...props}
            className="px-3 py-2 text-sm border"
            style={{ borderColor: 'var(--border-color)' }}
          >
            {children}
          </td>
        ),
        // 链接在新窗口打开
        a: ({ children, href, ...props }) => (
          <a
            {...props}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:no-underline"
            style={{ color: 'var(--accent)' }}
          >
            {children}
          </a>
        ),
        // 引用样式
        blockquote: ({ children, ...props }) => (
          <blockquote
            {...props}
            className="border-l-4 pl-4 my-3 italic"
            style={{ borderColor: 'var(--accent)', color: 'var(--text-secondary)' }}
          >
            {children}
          </blockquote>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

function CopyButton() {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    const pre = (e.currentTarget as HTMLElement).closest('div')?.querySelector('pre')
    if (!pre) return
    const code = pre.textContent || ''
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 p-1.5 rounded-md opacity-0 group-hover:opacity-100 transition-opacity"
      style={{ backgroundColor: 'var(--bg-tertiary)' }}
      title="复制代码"
    >
      {copied ? (
        <Check size={12} style={{ color: 'var(--success)' }} />
      ) : (
        <Copy size={12} style={{ color: 'var(--text-tertiary)' }} />
      )}
    </button>
  )
}
