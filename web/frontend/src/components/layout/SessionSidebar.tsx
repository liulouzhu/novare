/** 左侧会话列表 */

import { useEffect, useState } from 'react'
import { useSessionStore } from '@/stores/sessionStore'
import { useThemeStore } from '@/stores/themeStore'
import { cn } from '@/lib/utils'
import { Plus, Trash2, MessageSquare, Loader2, Sun, Moon, X } from 'lucide-react'

export function SessionSidebar() {
  const { sessions, currentId, loading, loadSessions, createSession, switchSession, deleteSession } = useSessionStore()
  const { theme, resolved, setTheme } = useThemeStore()
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  const handleNew = async () => {
    await createSession()
  }

  const handleDeleteClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setDeleteTarget(id)
  }

  const handleConfirmDelete = async () => {
    if (deleteTarget) {
      await deleteSession(deleteTarget)
      setDeleteTarget(null)
    }
  }

  const toggleTheme = () => {
    setTheme(resolved === 'dark' ? 'light' : 'dark')
  }

  return (
    <div
      className="w-60 flex flex-col border-r shrink-0 relative"
      style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-secondary)' }}
    >
      {/* 头部 */}
      <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: 'var(--border-color)' }}>
        <h1 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
          🔬 Novare
        </h1>
        <div className="flex items-center gap-1">
          <button
            onClick={toggleTheme}
            className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
            title={resolved === 'dark' ? '切换浅色主题' : '切换深色主题'}
          >
            {resolved === 'dark' ? (
              <Sun size={15} style={{ color: 'var(--text-secondary)' }} />
            ) : (
              <Moon size={15} style={{ color: 'var(--text-secondary)' }} />
            )}
          </button>
          <button
            onClick={handleNew}
            className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
            title="新建会话"
          >
            <Plus size={16} style={{ color: 'var(--text-secondary)' }} />
          </button>
        </div>
      </div>

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
          </div>
        )}
        {sessions.filter((s) => s.message_count > 0).map((s) => (
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
              onClick={(e) => handleDeleteClick(e, s.session_id)}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-opacity"
            >
              <Trash2 size={12} style={{ color: 'var(--error)' }} />
            </button>
          </div>
        ))}

        {!loading && sessions.filter((s) => s.message_count > 0).length === 0 && (
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

      {/* 删除确认弹窗 */}
      {deleteTarget && (
        <div className="absolute inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.4)' }}>
          <div
            className="w-72 rounded-xl border shadow-xl p-5"
            style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-primary)' }}
          >
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                删除会话
              </div>
              <button
                onClick={() => setDeleteTarget(null)}
                className="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                <X size={14} style={{ color: 'var(--text-tertiary)' }} />
              </button>
            </div>
            <p className="text-sm mb-5" style={{ color: 'var(--text-secondary)' }}>
              确定要删除此会话吗？删除后不可恢复。
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteTarget(null)}
                className="px-3 py-1.5 text-sm rounded-lg border transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
                style={{ borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
              >
                取消
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-3 py-1.5 text-sm rounded-lg text-white transition-colors hover:opacity-90"
                style={{ backgroundColor: 'var(--error)' }}
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
