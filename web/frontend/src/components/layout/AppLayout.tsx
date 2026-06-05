/** 三栏布局容器 */

import { useState } from 'react'
import { SessionSidebar } from './SessionSidebar'
import { ReferencePanel } from './ReferencePanel'
import { ChatArea } from '../chat/ChatArea'
import { useSessionStore } from '@/stores/sessionStore'
import { WelcomeScreen } from '../chat/WelcomeScreen'
import { PanelRightClose, PanelRightOpen } from 'lucide-react'

export function AppLayout() {
  const currentId = useSessionStore((s) => s.currentId)
  const [showPanel, setShowPanel] = useState(true)

  return (
    <div className="flex h-screen overflow-hidden" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* 左侧：会话列表 */}
      <SessionSidebar />

      {/* 中间：对话区域 */}
      <div className="flex-1 flex flex-col min-w-0">
        {currentId ? (
          <ChatArea
            sessionId={currentId}
            panelOpen={showPanel}
            onTogglePanel={() => setShowPanel(!showPanel)}
          />
        ) : (
          <WelcomeScreen />
        )}
      </div>

      {/* 右侧：引用面板 */}
      {currentId && showPanel && <ReferencePanel sessionId={currentId} />}
    </div>
  )
}
