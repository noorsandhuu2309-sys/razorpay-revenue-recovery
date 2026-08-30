// Thin typed client over the OMNIX workspace API.
//
// Every call carries the active workspace as a query parameter. The backend
// falls back to the local default when it is absent, so a missing workspace is
// never an error the UI has to handle.

import type {
  AgentWorker, Brief, GraphPayload, GraphStats, Intent,
  IntentHit, NovaTurn, OmxClaim, OmxEvent, OmxObject, OmxOutput,
  OmxRelationship, OmxSource, Ontology, OutputStyle, Summary, Workspace,
  ChallengeMeta, UnitBlock,
} from './types'

const BASE = ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.error || body.detail || detail
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '' && v !== null) p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // -- workspaces --------------------------------------------------------
  workspaces: () => req<{ workspaces: Workspace[] }>('/api/workspaces'),
  createWorkspace: (name: string, description = '') =>
    req<Workspace>('/api/workspaces', {
      method: 'POST', body: JSON.stringify({ name, description }),
    }),

  // -- ontology ----------------------------------------------------------
  ontology: () => req<Ontology>('/api/ontology'),

  // -- objects -----------------------------------------------------------
  objects: (workspace: string, opts: {
    type?: string; domain?: string; tracked?: boolean; q?: string
    /** Exact natural-key lookup — how Map/TERRA views resolve an entity into
     *  the same workspace object the Graph selects. */
    externalId?: string
    limit?: number
  } = {}) =>
    req<{ objects: OmxObject[] }>(`/api/objects${qs({ workspace, ...opts })}`),

  object: (workspace: string, id: string) =>
    req<OmxObject>(`/api/objects/${id}${qs({ workspace })}`),

  search: (workspace: string, q: string, limit = 20) =>
    req<{ results: OmxObject[] }>(`/api/objects/search${qs({ workspace, q, limit })}`),

  updateObject: (workspace: string, id: string, patch: Partial<OmxObject>) =>
    req<OmxObject>(`/api/objects/${id}${qs({ workspace })}`, {
      method: 'PATCH', body: JSON.stringify(patch),
    }),

  track: (workspace: string, id: string, tracked: boolean) =>
    req<OmxObject>(`/api/objects/${id}/track${qs({ workspace })}`, {
      method: 'POST', body: JSON.stringify({ tracked }),
    }),

  objectSources: (workspace: string, id: string) =>
    req<{ sources: OmxSource[] }>(`/api/objects/${id}/sources${qs({ workspace })}`),

  objectRelationships: (workspace: string, id: string) =>
    req<{ relationships: OmxRelationship[] }>(
      `/api/objects/${id}/relationships${qs({ workspace })}`),

  objectEvents: (workspace: string, id: string) =>
    req<{ events: OmxEvent[] }>(`/api/objects/${id}/events${qs({ workspace })}`),

  // -- relationships -----------------------------------------------------
  // These three endpoints have existed since the graph substrate landed and
  // nothing called them. They are what lets an edge be inspected at all: the
  // subgraph payload carries no confidence and no timestamps, so the only
  // route to "when was this first observed" is the relationship record.
  relationships: (workspace: string, relations?: string, limit = 2000) =>
    req<{ relationships: OmxRelationship[] }>(
      `/api/relationships${qs({ workspace, relations, limit })}`),

  /** Evidence behind one relationship. Returns an empty list when nothing is
   *  attached, which is the honest answer and must be rendered as such rather
   *  than as an absent section. */
  relationshipSources: (workspace: string, id: string) =>
    req<{ sources: OmxSource[] }>(
      `/api/relationships/${id}/sources${qs({ workspace })}`),

  // -- graph -------------------------------------------------------------
  graph: (workspace: string, opts: {
    roots?: string; hops?: number; max_nodes?: number; per_node?: number
    types?: string; relations?: string; communities?: boolean
  } = {}) => req<GraphPayload>(`/api/graph${qs({ workspace, ...opts })}`),

  expand: (workspace: string, id: string, exclude: string[], limit = 8) =>
    req<{ nodes: OmxObject[]; edges: GraphPayload['edges'] }>(
      `/api/graph/expand${qs({ workspace })}`, {
        method: 'POST', body: JSON.stringify({ id, exclude, limit }),
      }),

  path: (workspace: string, src: string, dst: string) =>
    req<{ path: { from: OmxObject; to: OmxObject; label: string }[]; found: boolean }>(
      `/api/graph/path${qs({ workspace, src, dst })}`),

  /** Label-propagation clusters over the whole Space, named by their most
   *  important members ("Russia · Ukraine · Black Sea"). The graph payload
   *  carries a per-node `community` id when `communities: true`; this is the
   *  matching directory of what those ids MEAN. */
  communities: (workspace: string, limit = 12) =>
    req<{ communities: GraphPayload['communities'] }>(
      `/api/graph/communities${qs({ workspace, limit })}`),

  stats: (workspace: string) => req<GraphStats>(`/api/graph/stats${qs({ workspace })}`),

  // -- timeline ----------------------------------------------------------
  timeline: (workspace: string, opts: { objects?: string; hours?: number; limit?: number } = {}) =>
    req<{ events: OmxEvent[] }>(`/api/timeline${qs({ workspace, ...opts })}`),

  // -- nova --------------------------------------------------------------
  command: (workspace: string, input: string, selection: string[], intent?: string) =>
    req<Record<string, unknown>>('/api/nova/command', {
      method: 'POST', body: JSON.stringify({ workspace, input, selection, intent }),
    }),

  /** The conversation for a Space. Stored server-side, so it is the same
   *  thread from every view and survives a reload. */
  thread: (workspace: string, limit = 100) =>
    req<{ turns: NovaTurn[] }>(`/api/nova/thread${qs({ workspace, limit })}`),

  clearThread: (workspace: string) =>
    req<{ cleared: number }>(`/api/nova/thread${qs({ workspace })}`,
      { method: 'DELETE' }),

  research: (workspace: string, question: string, selection: string[], depth = 'standard') =>
    req<{ executionId: string }>('/api/research/run', {
      method: 'POST', body: JSON.stringify({ workspace, question, selection, depth }),
    }),

  ingest: (workspace: string, executionId: string) =>
    req<Record<string, unknown>>('/api/research/ingest', {
      method: 'POST', body: JSON.stringify({ workspace, executionId }),
    }),

  // -- evidence ----------------------------------------------------------
  claims: (workspace: string, execution?: string) =>
    req<{ claims: OmxClaim[] }>(`/api/claims${qs({ workspace, execution })}`),

  claimEvidence: (workspace: string, id: string) =>
    req<{ claim: OmxClaim; sources: OmxSource[] }>(
      `/api/claims/${id}/evidence${qs({ workspace })}`),

  // The endpoint defaults to 200 and clamps at 1000. Taking the default meant
  // a workspace with 358 sources displayed 200 of them under the label "200
  // sources" — not a truncated list, a wrong number. Ask for the ceiling.
  sources: (workspace: string, limit = 1000) =>
    req<{ sources: OmxSource[] }>(`/api/sources${qs({ workspace, limit })}`),

  // -- live --------------------------------------------------------------
  brief: (workspace: string, hours = 168) =>
    req<Brief>(`/api/brief${qs({ workspace, hours })}`),

  summary: (workspace: string) => req<Summary>(`/api/summary${qs({ workspace })}`),

  syncTracking: (workspace: string) =>
    req<{ ok: boolean; checked: number; events: number }>('/api/tracking/sync', {
      method: 'POST', body: JSON.stringify({ workspace }),
    }),

  // -- executions --------------------------------------------------------
  // `steps[].output` carries the unit's blocks and meta. CHALLENGE reads
  // its whole result from there rather than going via the artifact store,
  // because the hand-off to research needs meta.researchQuestions the
  // moment the run finishes.
  execution: (id: string) => req<{
    id: string; status: string; agent: string; title: string
    error?: string
    steps?: {
      title: string; status: string
      output?: { summary?: string; blocks?: UnitBlock[]; meta?: ChallengeMeta }
    }[]
  }>(`/api/executions/${id}`),

  /** The run's audit trail, incrementally. `after` is the last `seq` seen.
   *
   *  CHALLENGE polls this to draw its panel from what the vendors are actually
   *  doing. The alternative — animating four seats on a client-side timer —
   *  would be a progress bar that shows the same thing whether the models are
   *  answering or the backend is down, which is the one kind of interface this
   *  product does not get to ship. Model prompt content is filtered server-side
   *  before it reaches here. */
  agentEvents: (executionId: string, after = 0) =>
    req<{
      events: { seq: number; type: string; payload: Record<string, unknown> }[]
      status: string
    }>(`/api/agents/${executionId}/events${qs({ after })}`),

  runAgent: (code: string, input: string, workspace: string) =>
    req<{ executionId: string; agent: string }>(`/api/agents/${code}/run`, {
      method: 'POST',
      body: JSON.stringify({ input, workspace_id: workspace }),
    }),

  // -- outputs (§12) -----------------------------------------------------
  // The style list comes from the server so the Create menu can never offer
  // something the backend does not implement.
  outputStyles: () => req<{ styles: OutputStyle[] }>('/api/outputs/styles'),

  outputs: (workspace: string, style?: string) =>
    req<{ outputs: OmxOutput[] }>(`/api/outputs${qs({ workspace, style })}`),

  output: (id: string) => req<OmxOutput>(`/api/outputs/${id}`),

  createOutput: (workspace: string, style: string, objectIds: string[],
                 title = '') =>
    req<{ output: OmxOutput }>('/api/outputs', {
      method: 'POST',
      body: JSON.stringify({ workspace, style, objectIds, title }),
    }),

  /** Absolute path, not a fetch: used as an href so the browser downloads or
   *  opens the rendered file itself. */
  outputHref: (id: string, format: string, download = false) =>
    `/api/outputs/${id}/render${qs({ format, download })}`,

  // -- intents (§11) -----------------------------------------------------
  intents: (workspace: string, status?: string) =>
    req<{ intents: Intent[] }>(`/api/intents${qs({ workspace, status })}`),

  createIntent: (workspace: string, body: {
    title: string; description?: string; objectIds?: string[]
    keywords?: string[]; relevanceFloor?: string; cadenceMinutes?: number
  }) => req<{ intent: Intent }>('/api/intents', {
    method: 'POST', body: JSON.stringify({ workspace, ...body }),
  }),

  updateIntent: (workspace: string, id: string, patch: Partial<Intent>) =>
    req<Intent>(`/api/intents/${id}${qs({ workspace })}`, {
      method: 'PATCH', body: JSON.stringify(patch),
    }),

  deleteIntent: (workspace: string, id: string) =>
    req<{ deleted: boolean }>(`/api/intents/${id}${qs({ workspace })}`,
      { method: 'DELETE' }),

  checkIntent: (workspace: string, id: string) =>
    req<{ newHits: number; hits: IntentHit[]; checkedAt: string }>(
      `/api/intents/${id}/check${qs({ workspace })}`, { method: 'POST' }),

  intentHits: (workspace: string, id: string) =>
    req<{ hits: IntentHit[] }>(`/api/intents/${id}/hits${qs({ workspace })}`),

  // -- agents as workers (§9) --------------------------------------------
  agentsLive: (workspace: string) =>
    req<{ active: AgentWorker[]; recent: AgentWorker[] }>(
      `/api/agents/live${qs({ workspace })}`),

  agent: (id: string) => req<AgentWorker>(`/api/agents/${id}`),

  agentPause: (id: string) =>
    req<{ paused: boolean; note: string }>(`/api/agents/${id}/pause`,
      { method: 'POST' }),

  agentResume: (id: string) =>
    req<{ resumed: boolean }>(`/api/agents/${id}/resume`, { method: 'POST' }),

  agentCancel: (id: string) =>
    req<{ cancelling: boolean; note: string }>(`/api/agents/${id}/cancel`,
      { method: 'POST' }),

  agentRedirect: (id: string, instruction: string) =>
    req<{ queued: boolean; note: string }>(`/api/agents/${id}/redirect`, {
      method: 'POST', body: JSON.stringify({ instruction }),
    }),

  // -- terra -------------------------------------------------------------
  terraSync: (workspace?: string) =>
    req<Record<string, unknown>>('/api/terra/bridge/sync', {
      method: 'POST', body: JSON.stringify({ workspace }),
    }),
}
