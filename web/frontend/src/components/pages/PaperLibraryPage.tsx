/** 论文库页面 */

import { useState, useEffect } from 'react'
import { type Paper, fetchPapers } from '@/lib/api'
import { Search, FileText, ExternalLink, Loader2, Filter, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

export function PaperLibraryPage() {
  const [papers, setPapers] = useState<Paper[]>([])
  const [loading, setLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<'all' | 'parsed' | 'unparsed'>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const loadPapers = async () => {
    setLoading(true)
    try {
      const data = await fetchPapers({ q: searchQuery || undefined })
      setPapers(data)
    } catch (e) {
      console.error('Failed to load papers:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPapers()
  }, [])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    loadPapers()
  }

  const filtered = papers.filter((p) => {
    if (filter === 'parsed') return p.is_parsed
    if (filter === 'unparsed') return !p.is_parsed
    return true
  })

  return (
    <div className="flex flex-col h-full" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* 顶栏 */}
      <div className="px-6 py-4 border-b shrink-0" style={{ borderColor: 'var(--border-color)' }}>
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>📄 论文库</h1>
          <button
            onClick={loadPapers}
            className="p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title="刷新"
          >
            <RefreshCw size={16} style={{ color: 'var(--text-secondary)' }} />
          </button>
        </div>

        <div className="flex items-center gap-3">
          {/* 搜索框 */}
          <form onSubmit={handleSearch} className="flex-1 relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索论文标题..."
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border outline-none focus:ring-1"
              style={{
                borderColor: 'var(--border-color)',
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
              }}
            />
          </form>

          {/* 筛选 */}
          <div className="flex items-center gap-1 border rounded-lg p-0.5" style={{ borderColor: 'var(--border-color)' }}>
            {([
              { key: 'all', label: '全部' },
              { key: 'parsed', label: '已解析' },
              { key: 'unparsed', label: '未解析' },
            ] as const).map((opt) => (
              <button
                key={opt.key}
                onClick={() => setFilter(opt.key)}
                className={cn(
                  'px-3 py-1.5 text-xs rounded-md transition-colors',
                  filter === opt.key ? 'font-medium' : '',
                )}
                style={{
                  backgroundColor: filter === opt.key ? 'var(--bg-tertiary)' : 'transparent',
                  color: filter === opt.key ? 'var(--text-primary)' : 'var(--text-secondary)',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 论文列表 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={20} className="animate-spin" style={{ color: 'var(--text-tertiary)' }} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20" style={{ color: 'var(--text-tertiary)' }}>
            <FileText size={40} className="mx-auto mb-3 opacity-30" />
            <div className="text-sm">暂无论文</div>
            <div className="text-xs mt-1">使用对话中的 paper_search 工具检索论文</div>
          </div>
        ) : (
          <div className="space-y-2 max-w-4xl mx-auto">
            <div className="text-xs mb-3" style={{ color: 'var(--text-tertiary)' }}>
              共 {filtered.length} 篇论文
            </div>
            {filtered.map((paper) => (
              <div
                key={paper.id}
                className="border rounded-lg p-4 cursor-pointer transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/50"
                style={{ borderColor: 'var(--border-color)' }}
                onClick={() => setExpandedId(expandedId === paper.id ? null : paper.id)}
              >
                <div className="flex items-start gap-3">
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                    style={{ backgroundColor: paper.is_parsed ? 'var(--accent-light)' : 'var(--bg-tertiary)' }}
                  >
                    <FileText size={14} style={{ color: paper.is_parsed ? 'var(--accent)' : 'var(--text-tertiary)' }} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>
                      {paper.title}
                    </div>
                    <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                      {paper.authors.slice(0, 3).join(', ')}
                      {paper.authors.length > 3 && ' et al.'}
                      {paper.year && ` · ${paper.year}`}
                      {paper.citation_count > 0 && ` · 引用 ${paper.citation_count}`}
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <span
                        className="text-xs px-1.5 py-0.5 rounded"
                        style={{
                          backgroundColor: paper.is_parsed ? 'rgba(34,197,94,0.1)' : 'var(--bg-tertiary)',
                          color: paper.is_parsed ? 'var(--success)' : 'var(--text-tertiary)',
                        }}
                      >
                        {paper.is_parsed ? '✅ 已解析' : '⏳ 仅元数据'}
                      </span>
                      <span className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                        {paper.source}
                      </span>
                    </div>
                  </div>
                  {paper.url && (
                    <a
                      href={paper.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ExternalLink size={14} style={{ color: 'var(--text-tertiary)' }} />
                    </a>
                  )}
                </div>

                {/* 展开详情 */}
                {expandedId === paper.id && paper.abstract && (
                  <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
                    <div className="text-xs font-medium mb-1.5" style={{ color: 'var(--text-tertiary)' }}>摘要</div>
                    <div className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                      {paper.abstract}
                    </div>
                    <div className="mt-2 flex items-center gap-4 text-xs" style={{ color: 'var(--text-tertiary)' }}>
                      <span>ID: {paper.id}</span>
                      {paper.pdf_path && <span>PDF: {paper.pdf_path.split('/').pop()}</span>}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
