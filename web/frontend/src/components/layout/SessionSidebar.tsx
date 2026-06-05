/** 左侧会话列表 */

import { useEffect } from 'react'
import { useSessionStore } from '@/stores/sessionStore'
import { useChatStore } from '@/stores/chatStore'
import { cn } from '@/lib/utils'
import { Plus, Trash2, MessageSquare, Loader2 } from 'lucide-react'

export function SessionSidebar() {
  const { sessions, currentId, loading, loadSessions, createSession, switchSession, deleteSession } = useSessionStore()

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  const handleNew = async () => {
    await createSession()
  }

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (confirm('确定删除此会话？')) {
      await deleteSession(id)
    }
  }

  return (
    <div
      className="w-60 flex flex-col border-r shrink-0"
      style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-secondary)' }}
    >
      {/* 头部 */}
      <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: 'var(--border-color)' }}>
        <h1 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
          🔬 Novare
        </h1>
        <button
          onClick={handleNew}
          className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="新建会话"
        >
          <Plus size={16} style={{ color: 'var(--text-secondary)' }} />
        </button>
      </div>

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
          </div>
        )}
        {sessions.map((s) => (
          <div
            key={s.session_id}
            onClick={() => switchSession(s.session_id)}
            className={cn(
              'group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors text-sm',
              currentId === s.session_id
                ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300'
                : 'hover:bg-gray-100 dark:hover:bg-gray-800',
            )}
            style={currentId !== s.session_id ? { color: 'var(--text-primary)' } : undefined}
          >
            <MessageSquare size={14} className="shrink-0 opacity-50" />
            <span className="truncate flex-1">{s.title || s.session_id}</span>
            <button
              onClick={(e) => handleDelete(e, s.session_id)}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-opacity"
            >
              <Trash2 size={12} style={{ color: 'var(--error)' }} />
            </button>
          </div>
        ))}

        {!loading && sessions.length === 0 && (
          <div className="text-center py-8 text-sm" style={{ color: 'var(--text-tertiary)' }}>
            暂无会话
          </div>
        )}
      </div>

      {/* 底部 Skill 快捷入口 */}
      <div className="p-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
        <div className="text-xs mb-2" style={{ color: 'var(--text-tertiary)' }}>
          快捷 Skill
        </div>
        <div className="flex gap-1.5">
          {['research', 'parse', 'ask'].map((skill) => (
            <button
              key={skill}
              className="px-2 py-1 text-xs rounded-md border transition-colors hover:bg-gray-100 dark:hover:bg-gray-800"
              style={{ borderColor: 'var(--border-color)', color: 'var(--text-secondary)' }}
              onClick={() => {
                // TODO: 触发 Skill 输入
              }}
            >
              /{skill}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
