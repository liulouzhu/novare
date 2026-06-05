/** 会话列表状态管理 */

import { create } from 'zustand'
import { type SessionMeta, fetchSessions, createSession as apiCreate, deleteSession as apiDelete, fetchSession } from '@/lib/api'

interface SessionStore {
  sessions: SessionMeta[]
  currentId: string | null
  loading: boolean

  loadSessions: () => Promise<void>
  createSession: () => Promise<string>
  switchSession: (id: string) => void
  deleteSession: (id: string) => Promise<void>
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessions: [],
  currentId: null,
  loading: false,

  loadSessions: async () => {
    set({ loading: true })
    try {
      const sessions = await fetchSessions()
      set({ sessions, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  createSession: async () => {
    const session = await apiCreate()
    set((s) => ({ sessions: [session, ...s.sessions], currentId: session.session_id }))
    return session.session_id
  },

  switchSession: (id: string) => {
    set({ currentId: id })
  },

  deleteSession: async (id: string) => {
    await apiDelete(id)
    set((s) => {
      const sessions = s.sessions.filter((x) => x.session_id !== id)
      const currentId = s.currentId === id ? (sessions[0]?.session_id ?? null) : s.currentId
      return { sessions, currentId }
    })
  },
}))
