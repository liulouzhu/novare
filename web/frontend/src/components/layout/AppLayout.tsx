/** 三栏布局容器 */

import { SessionSidebar } from './SessionSidebar'
import { ReferencePanel } from './ReferencePanel'
import { ChatArea } from '../chat/ChatArea'
import { useSessionStore } from '@/stores/sessionStore'
import { WelcomeScreen } from '../chat/WelcomeScreen'

export function AppLayout() {
  const currentId = useSessionStore((s) => s.currentId)

  return (
    <div className="flex h-screen overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* 左侧：会话列表 */}
      <SessionSidebar />

      {/* 中间：对话区域 */}
      <div className="flex-1 flex flex-col min-w-0">
        {currentId ? <ChatArea sessionId={currentId} /> : <WelcomeScreen />}
      </div>

      {/* 右侧：引用面板 */}
      {currentId && <ReferencePanel sessionId={currentId} />}
    </div>
  )
}
