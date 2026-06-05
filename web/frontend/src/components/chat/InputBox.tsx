/** 多行输入框 */

import { useState, useRef, useCallback, useEffect } from 'react'
import { Send, Paperclip, Loader2, Search, FileText, HelpCircle } from 'lucide-react'
import { uploadFile } from '@/lib/api'

const SKILLS = [
  { name: 'research', label: '文献综述', desc: '搜索论文 → 解析 → RAG → 生成综述', icon: <Search size={14} /> },
  { name: 'parse', label: '论文解析', desc: '解析 PDF，提取结构化信息', icon: <FileText size={14} /> },
  { name: 'ask', label: '语义问答', desc: '在已解析论文中检索答案', icon: <HelpCircle size={14} /> },
]

interface Props {
  onSend: (content: string, refs?: Array<{ type: string; id: string; title?: string }>) => void
  disabled: boolean
}

export function InputBox({ onSend, disabled }: Props) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Array<{ name: string; path: string }>>([])
  const [uploading, setUploading] = useState(false)
  const [showSkills, setShowSkills] = useState(false)
  const [skillFilter, setSkillFilter] = useState('')
  const [selectedSkillIdx, setSelectedSkillIdx] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const skillListRef = useRef<HTMLDivElement>(null)

  // 检测 "/" 触发 Skill 列表
  useEffect(() => {
    if (text === '/' || (text.startsWith('/') && !text.includes(' ') && text.length < 20)) {
      setShowSkills(true)
      setSkillFilter(text.slice(1))
      setSelectedSkillIdx(0)
    } else {
      setShowSkills(false)
    }
  }, [text])

  const filteredSkills = skillFilter
    ? SKILLS.filter((s) => s.name.includes(skillFilter) || s.label.includes(skillFilter))
    : SKILLS

  // 确保选中项在有效范围内
  useEffect(() => {
    if (selectedSkillIdx >= filteredSkills.length) {
      setSelectedSkillIdx(Math.max(0, filteredSkills.length - 1))
    }
  }, [filteredSkills.length, selectedSkillIdx])

  const selectSkill = (skillName: string) => {
    setText(`/${skillName} `)
    setShowSkills(false)
    textareaRef.current?.focus()
  }

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
    // Skill 列表打开时的键盘导航
    if (showSkills && filteredSkills.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedSkillIdx((prev) => (prev + 1) % filteredSkills.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedSkillIdx((prev) => (prev - 1 + filteredSkills.length) % filteredSkills.length)
        return
      }
      if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        selectSkill(filteredSkills[selectedSkillIdx].name)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setShowSkills(false)
        return
      }
    }

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

        {/* Skill 下拉列表 */}
        {showSkills && filteredSkills.length > 0 && (
          <div
            ref={skillListRef}
            className="mb-2 rounded-lg border overflow-hidden shadow-sm"
            style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-primary)' }}
          >
            <div className="px-3 py-1.5 text-xs font-medium border-b" style={{ borderColor: 'var(--border-color)', color: 'var(--text-tertiary)' }}>
              Skill 命令 <span className="font-normal">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
            </div>
            {filteredSkills.map((skill, idx) => (
              <button
                key={skill.name}
                onClick={() => selectSkill(skill.name)}
                onMouseEnter={() => setSelectedSkillIdx(idx)}
                className="w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors"
                style={{
                  backgroundColor: idx === selectedSkillIdx ? 'var(--bg-tertiary)' : 'transparent',
                }}
              >
                <span style={{ color: 'var(--accent)' }}>{skill.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
                    /{skill.name}
                    <span className="ml-2 text-xs font-normal" style={{ color: 'var(--text-secondary)' }}>
                      {skill.label}
                    </span>
                  </div>
                  <div className="text-xs truncate" style={{ color: 'var(--text-tertiary)' }}>
                    {skill.desc}
                  </div>
                </div>
              </button>
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
