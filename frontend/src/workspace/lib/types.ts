// Shapes returned by the OMNIX graph API. Kept in one file so a backend
// serialiser change surfaces as a type error in every consumer at once.

export type Provenance =
  | 'user_created'
  | 'verified'
  | 'source_backed'
  | 'ai_inferred'

export interface OmxObject {
  id: string
  type: string
  typeLabel: string
  family: string
  familyLabel: string
  domain: 'external' | 'internal' | 'ai'
  name: string
  description: string
  externalId: string
  properties: Record<string, unknown>
  tags: string[]
  provenance: Provenance
  provenanceLabel: string
  /** null means NOT MEASURED. Never render it as 0. */
  confidence: number | null
  salience: number
  tracked: boolean
  lat: number | null
  lon: number | null
  geo: boolean
  glyph: string
  color: string
  shape: string
  vweight: number
  ring: boolean
  degree: number | null
  executionId: string | null
  firstSeen: string
  lastSeen: string
  /** present only inside a subgraph response */
  root?: boolean
  depth?: number
  community?: number
}

export interface OmxEdge {
  id: string | null
  source: string
  target: string
  relation: string
  label: string
  weight: number
  count: number
  sentiment: number
  provenance: Provenance
}

export interface OmxRelationship {
  id: string
  src: string
  dst: string
  relation: string
  label: string
  symmetric: boolean
  weight: number
  observations: number
  sentiment: number
  provenance: Provenance
  confidence: number | null
  properties: Record<string, unknown>
  firstSeen: string
  lastSeen: string
}

export interface OmxEvent {
  id: string
  objectId: string | null
  type: string
  title: string
  body: string
  occurredAt: string
  detectedAt: string
  relevance: 'low' | 'medium' | 'high'
  provenance: Provenance
  properties: Record<string, unknown>
  executionId: string | null
  dismissed: boolean
}

export interface OmxSource {
  id: string
  url: string
  title: string
  publisher: string
  tier: string
  tierLabel: string
  year: number | null
  credibility?: number
  excerpt?: string
  snippet?: string
  retrievedAt: string
}

export interface OmxClaim {
  id: string
  text: string
  verdict: string
  confidence: number
  supportedBy: number[]
  contradictedBy: number[]
  executionId: string
  status: string
}

export interface Workspace {
  id: string
  name: string
  description: string
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface GraphPayload {
  workspace: string
  roots: string[]
  nodes: OmxObject[]
  edges: OmxEdge[]
  communities: { id: number; size: number; label: string; members: string[] }[]
  stats: { nodes: number; edges: number; byType: Record<string, number> }
}

export interface OntologyType {
  key: string
  label: string
  family: string
  domain: string
  geo: boolean
  temporal: boolean
  rank: number
  glyph: string
  color: string
  shape: string
  weight: number
  ring: boolean
}

export interface Ontology {
  families: Record<string, { label: string; glyph: string; color: string; shape: string; weight: number; ring: boolean }>
  domains: string[]
  types: OntologyType[]
  relations: { key: string; label: string; symmetric: boolean; weight: number; extractable: boolean }[]
  provenance: Record<string, { label: string; rank: number; trust: string }>
}

export interface Brief {
  workspace: string
  windowHours: number
  trackedCount: number
  trackedChanged: number
  eventCount: number
  high: BriefEntry[]
  medium: BriefEntry[]
  low: BriefEntry[]
  generatedAt: string
}

export interface BriefEntry {
  object: OmxObject
  eventCount: number
  latest: OmxEvent
  events: OmxEvent[]
}

/** What `/api/graph/stats` returns. Deliberately NOT `Summary`: that endpoint
 *  counts the graph only, and has no `sources` or `claims`. Typing it as a
 *  Summary made a blank count render next to a label with no type error. */
export interface GraphStats {
  workspace: string
  objects: number
  relationships: number
  events: number
  tracked: number
  byType: Record<string, number>
  byFamily: Record<string, number>
  byProvenance: Record<string, number>
}

/** What `/api/summary` returns — the graph stats plus the evidence layer. */
export interface Summary extends Omit<GraphStats, 'workspace'> {
  claims: number
  claimsVerified: number
  sources: number
}

/** A step in the focus trail. Kept as {id,name} rather than an id alone so the
 *  breadcrumb can render without waiting on a hydrate round trip. */
export interface FocusStep {
  id: string
  name: string
  glyph: string
}

/** One exchange with NOVA. The thread is stored per Space on the backend, so
 *  it survives reloads and is the same conversation from any view. */
export interface NovaTurn {
  id: string
  role: 'user' | 'assistant'
  text: string
  intent: string
  model: string
  executionId: string | null
  context: string[]
  createdAt: string
}

// -- Outputs (§12) ---------------------------------------------------------
/** One thing Create can produce. Served by the backend so this menu can never
 *  offer a style the server does not implement. */
export interface OutputStyle {
  key: string
  label: string
  glyph: string
  artifactType: string
  objectType: string
  formats: string[]
  hint: string
  minObjects: number
}

/** A section of a generated document. One shape for every style, which is why
 *  a single renderer draws all of them. */
export type OutputSection =
  | { kind: 'text'; heading: string; body: string; generated?: boolean }
  | { kind: 'list'; heading: string; items: string[] }
  | { kind: 'table'; heading: string; columns: string[]; rows: (string | number)[][] }
  | { kind: 'metrics'; heading: string; metrics: { label: string; value: number | string }[] }
  | { kind: 'chart'; heading: string; unit?: string; series: { label: string; value: number }[] }

export interface OutputContent {
  style: string
  styleLabel: string
  title: string
  question: string
  generatedAt: string
  sections: OutputSection[]
  inputs: { objectIds: string[]; objects: { id: string; name: string; type: string }[] }
  counts: Record<string, number>
  provenance: {
    floor: Provenance
    floorLabel: string
    breakdown: { label: string; value: number }[]
    /** True when one section was written by a model rather than measured. */
    synthesised: boolean
  }
}

export interface OmxOutput {
  id: string
  workspaceId: string
  type: string
  title: string
  sourceAgent: string
  executionId: string | null
  version: number
  tags: string[]
  createdAt: string
  /** Present on a single fetch, omitted from listings. */
  content?: OutputContent
  /** Set when the output was created — the graph node it became. */
  objectId?: string | null
  provenance?: Provenance
  lineage?: {
    references: { id: string; title?: string; relation: string }[]
    referenced_by: { id: string; title?: string; relation: string }[]
  }
}

// -- Intents (§11) ---------------------------------------------------------
export interface IntentHit {
  id: string
  intentId: string
  kind: 'event' | 'relationship' | 'claim'
  refId: string
  objectId: string | null
  title: string
  detail: string
  relevance: 'low' | 'medium' | 'high'
  matched: Record<string, unknown>
  acknowledged: boolean
  createdAt: string
}

export interface Intent {
  id: string
  workspaceId: string
  title: string
  description: string
  status: 'active' | 'paused' | 'archived'
  objectIds: string[]
  objects: { id: string; name: string; type: string }[]
  keywords: string[]
  relevanceFloor: 'low' | 'medium' | 'high'
  cadenceMinutes: number
  /** null means NEVER CHECKED. Must never render as "all clear". */
  lastCheckedAt: string | null
  lastHitAt: string | null
  hitCount: number
  recentHits: IntentHit[]
  createdAt: string
}

// -- Agents as workers (§9) ------------------------------------------------
export interface AgentUsage {
  calls: number
  inputTokens: number
  outputTokens: number
  costUsd: number
  /** True when token counts were inferred from length, not reported. */
  tokensEstimated: boolean
  models: string[]
  errors: number
  latencyMsTotal?: number
}

export interface AgentWorker {
  id: string
  workspaceId: string
  agent: string
  status: string
  mode: string
  title: string
  error: string
  createdAt: string
  startedAt: string | null
  completedAt: string | null
  durationMs: number
  steps?: { id: string; key: string; title: string; status: string; agent: string; durationMs: number; error: string }[]
  usage: AgentUsage
  progress: {
    steps: number
    completed: number
    fraction: number
    current: { key: string; title: string; agent: string } | null
  }
  control: { paused: boolean; pendingRedirects: string[]; controllable: boolean }
  artifacts: OmxOutput[]
  sourceCount: number
  trail?: { seq: number; type: string; payload: Record<string, unknown>; ts: string }[]
}

export type ViewId =
  // the landing surface — "Your Intelligence", not a graph demo
  | 'home'
  // the conversation with NOVA
  | 'nova'
  // stress-test an idea before researching it
  | 'challenge'
  // workspace intelligence
  | 'graph'
  | 'compare'
  | 'timeline'
  | 'table'
  | 'document'
  | 'brief'
  | 'sources'
  | 'claims'
  // the three primitives the blueprint adds beyond Object and Space
  | 'outputs'
  | 'intents'
  | 'agents'
  | 'recovery'
  | 'audit'
  | 'policies'
  | 'model-quality'
  // TERRA — restored in full, see WORKSPACE.md
  | 'map'
  | 'news'
  | 'relationships'
  | 'analysis'
  | 'intel'
  | 'situation'
  | 'ask'
  | 'terra-agents'
  // HELIX — the bioinformatics corpus and its grounded answer layer
  | 'helix'
  // account, appearance, Spaces and the sign-out that leads back to the gate
  | 'settings'

// -- CHALLENGE ---------------------------------------------------------------
/** A block as a squad unit emits it (see omnix/squad/base.py). */
export interface UnitBlock {
  kind: string
  title?: string
  items?: string[]
  text?: string
}

/** A point several panellists made, with WHICH vendors made it. Vendors, not a
 *  count, so the UI can show attribution and never has to imply a score. */
export interface ChallengePoint {
  text: string
  raisedBy: string[]
}

export interface ChallengeMeta {
  kind?: string
  /** The headline sentence, composed server-side. It travels in meta because
   *  an execution's step output carries only meta and a block COUNT — the
   *  prose summary lives in the artifact, one fetch further away. */
  headline?: string
  /** Pre-rendered "where the panel split" lines, from the same code path that
   *  builds the block, so the two can never disagree. */
  split?: string[]
  researchQuestions?: string[]
  answered?: number
  panelSize?: number
  vendors?: string[]
  stances?: Record<string, string[]>
  assumptions?: ChallengePoint[]
  counterarguments?: ChallengePoint[]
  /** Always "model_opinion" here. The UI keys its disclaimer off this rather
   *  than off the agent name, so any future unit that returns unsourced output
   *  inherits the same warning. */
  evidence?: string
  disclaimer?: string
  error?: string
}
