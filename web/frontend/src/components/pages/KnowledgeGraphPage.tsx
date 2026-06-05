/** 知识图谱页面 */

import { useState, useEffect } from 'react'
import { RefreshCw, Network, Search, Info } from 'lucide-react'

interface GraphStats {
  total_nodes: number
  total_edges: number
  node_types: Record<string, number>
}

export function KnowledgeGraphPage() {
  const [stats, setStats] = useState<GraphStats | null>(null)
  const [loading, setLoading] = useState(false)

  // MVP: 显示图谱统计信息 + 占位可视化
  // 后续可接入 knowledge_graph 工具的 query/stats 接口

  return (
    <div className="flex flex-col h-full" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* 顶栏 */}
      <div className="px-6 py-4 border-b shrink-0" style={{ borderColor: 'var(--border-color)' }}>
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>🕸️ 知识图谱</h1>
          <button
            className="p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title="刷新"
          >
            <RefreshCw size={16} style={{ color: 'var(--text-secondary)' }} />
          </button>
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto flex items-center justify-center">
        <div className="text-center max-w-md px-4">
          <Network size={56} className="mx-auto mb-4 opacity-20" style={{ color: 'var(--accent)' }} />
          <h2 className="text-base font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
            知识图谱
          </h2>
          <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
            可视化论文、作者和概念之间的关系网络。使用对话中的 knowledge_graph 工具构建图谱。
          </p>

          {/* 快捷操作提示 */}
          <div className="space-y-3 text-left">
            {[
              { icon: '📄', title: 'add_paper', desc: '解析论文后自动添加到图谱' },
              { icon: '🏷️', title: 'add_concept', desc: '添加研究概念节点' },
              { icon: '🔗', title: 'add_relation', desc: '建立实体间关系' },
              { icon: '🔍', title: 'query', desc: '查询图谱中的实体和关系' },
              { icon: '🗺️', title: 'find_path', desc: '发现两个实体间的关联路径' },
            ].map((item) => (
              <div
                key={item.title}
                className="flex items-center gap-3 p-3 rounded-lg border"
                style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-secondary)' }}
              >
                <span className="text-lg">{item.icon}</span>
                <div>
                  <div className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{item.title}</div>
                  <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{item.desc}</div>
                </div>
              </div>
            ))}
          </div>

          <div
            className="mt-6 p-3 rounded-lg border text-xs text-left"
            style={{ borderColor: 'var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-tertiary)' }}
          >
            <Info size={12} className="inline mr-1" />
            在对话中输入 <span className="font-mono" style={{ color: 'var(--accent)' }}>/research</span> 可自动完成搜索 → 解析 → 图谱构建的完整流程。
          </div>
        </div>
      </div>
    </div>
  )
}
