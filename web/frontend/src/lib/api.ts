/** API 客户端 */

const BASE = ''  // Vite proxy handles /api → localhost:8000

export interface SessionMeta {
  session_id: string
  title: string
  message_count: number
  updated_at: string
}

export interface SessionDetail {
  session_id: string
  messages: Array<{
    role: string
    content: string
    tool_calls?: Array<{
      id: string
      type: string
      function: { name: string; arguments: string }
    }>
    tool_call_id?: string
  }>
  title: string
}

export interface Paper {
  id: string
  title: string
  authors: string[]
  abstract: string | null
  year: number | null
  source: string | null
  url: string | null
  pdf_path: string | null
  citation_count: number
  is_parsed: boolean
  created_at: string | null
}

// ── Sessions ──

export async function fetchSessions(): Promise<SessionMeta[]> {
  const res = await fetch(`${BASE}/api/sessions`)
  if (!res.ok) throw new Error('Failed to fetch sessions')
  return res.json()
}

export async function createSession(): Promise<SessionMeta> {
  const res = await fetch(`${BASE}/api/sessions`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to create session')
  return res.json()
}

export async function fetchSession(sessionId: string): Promise<SessionDetail> {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}`)
  if (!res.ok) throw new Error('Failed to fetch session')
  return res.json()
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete session')
}

// ── Papers ──

export async function fetchPapers(params?: { q?: string; is_parsed?: boolean }): Promise<Paper[]> {
  const searchParams = new URLSearchParams()
  if (params?.q) searchParams.set('q', params.q)
  if (params?.is_parsed !== undefined) searchParams.set('is_parsed', String(params.is_parsed))
  const qs = searchParams.toString()
  const res = await fetch(`${BASE}/api/papers${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error('Failed to fetch papers')
  return res.json()
}

export async function fetchPaper(paperId: string): Promise<Paper> {
  const res = await fetch(`${BASE}/api/papers/${encodeURIComponent(paperId)}`)
  if (!res.ok) throw new Error('Failed to fetch paper')
  return res.json()
}

// ── Upload ──

export async function uploadFile(file: File): Promise<{ filename: string; file_path: string; message: string }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/upload`, { method: 'POST', body: form })
  if (!res.ok) throw new Error('Failed to upload file')
  return res.json()
}

// ── Knowledge Graph ──

export interface GraphNode {
  id: string
  type: string
  label: string
  name: string
  title: string
  year: number | null
  citation_count: number
  description: string
}

export interface GraphLink {
  source: string
  target: string
  type: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export interface GraphStats {
  total_nodes: number
  total_edges: number
  node_types: Record<string, number>
  edge_types: Record<string, number>
}

export async function fetchGraph(): Promise<GraphData> {
  const res = await fetch(`${BASE}/api/graph`)
  if (!res.ok) throw new Error('Failed to fetch graph')
  return res.json()
}

export async function fetchGraphStats(): Promise<GraphStats> {
  const res = await fetch(`${BASE}/api/graph/stats`)
  if (!res.ok) throw new Error('Failed to fetch graph stats')
  return res.json()
}
