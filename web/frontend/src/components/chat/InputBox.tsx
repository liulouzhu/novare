/** 多行输入框 */

import { useState, useRef, useCallback } from 'react'
import { Send, Paperclip, Loader2 } from 'lucide-react'
import { uploadFile } from '@/lib/api'

interface Props {
  onSend: (content: string, refs?: Array<{ type: string; id: string; title?: string }>) => void
  disabled: boolean
}

export function InputBox({ onSend, disabled }: Props) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Array<{ name: string; path: string }>>([])
  const [uploading, setUploading] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleSend = useCallback(() => {
    if (!text.trim() || disabled) return

    let content = text.trim()
    if (attachments.length > 0) {
      content += '\n\n附件：\n' + attachments.map((a) => `- ${a.name} (${a.path})`).join('\n')
    }

    onSend(content)
    setText('')
    setAttachments([])
    // 重置 textarea 高度
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [text, disabled, attachments, onSend])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value)
    // 自动扩展高度
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files?.length) return

    setUploading(true)
    for (const file of Array.from(files)) {
      try {
        const res = await uploadFile(file)
        setAttachments((prev) => [...prev, { name: res.filename, path: res.file_path }])
      } catch (err) {
        console.error('Upload failed:', err)
      }
    }
    setUploading(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    const files = e.dataTransfer.files
    if (!files?.length) return

    setUploading(true)
    for (const file of Array.from(files)) {
      try {
        const res = await uploadFile(file)
        setAttachments((prev) => [...prev, { name: res.filename, path: res.file_path }])
      } catch (err) {
        console.error('Upload failed:', err)
      }
    }
    setUploading(false)
  }

  const removeAttachment = (idx: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== idx))
  }

  return (
    <div
      className="border-t px-4 py-3 shrink-0"
      style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-primary)' }}
    >
      <div className="max-w-3xl mx-auto">
        {/* 附件标签 */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {attachments.map((a, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border"
                style={{ borderColor: 'var(--border-color)', color: 'var(--text-secondary)' }}
              >
                📄 {a.name}
                <button
                  onClick={() => removeAttachment(i)}
                  className="ml-1 hover:text-red-500"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}

        {/* 输入区域 */}
        <div
          className="flex items-end gap-2 rounded-xl border px-3 py-2 transition-colors focus-within:ring-1"
          style={{
            borderColor: 'var(--border-color)',
            backgroundColor: 'var(--bg-secondary)',
            '--tw-ring-color': 'var(--border-focus)',
          } as React.CSSProperties}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
        >
          {/* 附件按钮 */}
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || disabled}
            className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors shrink-0 disabled:opacity-50"
            title="上传文件"
          >
            {uploading ? (
              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-secondary)' }} />
            ) : (
              <Paperclip size={16} style={{ color: 'var(--text-secondary)' }} />
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.csv,.xlsx,.txt,.json,.md"
            className="hidden"
            onChange={handleFileChange}
          />

          {/* 文本输入 */}
          <textarea
            ref={textareaRef}
            value={text}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? 'Agent 正在工作中...' : '输入研究问题... (Ctrl+Enter 发送)'}
            disabled={disabled}
            rows={1}
            className="flex-1 resize-none bg-transparent outline-none text-sm leading-6 max-h-[200px] disabled:opacity-50"
            style={{ color: 'var(--text-primary)' }}
          />

          {/* 发送按钮 */}
          <button
            onClick={handleSend}
            disabled={!text.trim() || disabled}
            className="p-1.5 rounded-md transition-colors shrink-0 disabled:opacity-30"
            style={{
              backgroundColor: text.trim() && !disabled ? 'var(--accent)' : 'transparent',
              color: text.trim() && !disabled ? 'white' : 'var(--text-tertiary)',
            }}
            title="发送 (Ctrl+Enter)"
          >
            <Send size={16} />
          </button>
        </div>

        <div className="text-xs mt-1.5 text-center" style={{ color: 'var(--text-tertiary)' }}>
          Ctrl+Enter 发送 · 支持拖拽上传文件 · 输入 / 使用 Skill
        </div>
      </div>
    </div>
  )
}
