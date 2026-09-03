// Revora view registry — single source of truth for navigation and shell chrome.

import type { ReactNode } from 'react'
import type { ViewId } from './types'
import {
  IconAgents, IconAnalysis, IconAsk, IconBrief, IconChallenge, IconClaims,
  IconCompare, IconGraph, IconRecovery, IconHome, IconIntel, IconIntents, IconMap, IconNews,
  IconOutputs, IconRelationships, IconRuns, IconSettings, IconSituation,
  IconSources, IconQuery, IconTable, IconTimeline, IconHelix,
} from '../components/Icons'

export type NavGroup =
  | 'Overview'
  | 'Intelligence'
  | 'Automation'
  | 'Analytics'
  | 'Governance'
  | 'foot'
  | 'hidden'

export interface ViewDef {
  id: ViewId
  label: string
  icon: (p: { size?: number }) => ReactNode
  group: NavGroup
  hint: string
  inspector: boolean
  trustLens: boolean
  actionBar: boolean
  novaBar: boolean
}

const def = (
  id: ViewId, label: string, icon: ViewDef['icon'], group: ViewDef['group'],
  hint: string,
  flags: Partial<Pick<ViewDef, 'inspector' | 'trustLens' | 'actionBar' | 'novaBar'>> = {},
): ViewDef => ({
  id, label, icon, group, hint,
  inspector: true, trustLens: true, actionBar: true, novaBar: false, ...flags,
})

export const VIEWS: ViewDef[] = [
  // -- Overview -------------------------------------------------------------
  def('home', 'Recovery Overview', IconHome, 'hidden',
      'Executive summary of today\'s recovery performance',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
  def('recovery', 'Recovery Dashboard', IconRecovery, 'Overview',
      'Detect, diagnose, and recover failed-payment revenue safely',
      { inspector: false, trustLens: false, actionBar: false }),
  def('table', 'Recovery Queue', IconTable, 'Overview',
      'Operational queue of transactions and recovery status',
      { trustLens: false }),

  // -- Intelligence ---------------------------------------------------------
  def('nova', 'Ask Revora', IconAsk, 'Intelligence',
      'Revenue recovery AI analyst — failures, policies, and opportunities',
      { trustLens: false, actionBar: false, novaBar: false }),
  def('claims', 'Evidence', IconClaims, 'Intelligence',
      'Supporting evidence for recovery recommendations'),
  def('sources', 'Payment Context', IconSources, 'Intelligence',
      'Transaction and payment context for recovery decisions'),
  def('graph', 'Decision Graph', IconGraph, 'Intelligence',
      'Payment → failure → diagnosis → evidence → policy → outcome'),
  def('brief', 'Recovery Brief', IconBrief, 'Intelligence',
      'Executive recovery summary — revenue at risk and opportunities'),

  // -- Automation -----------------------------------------------------------
  def('intents', 'Recovery Monitors', IconIntents, 'Automation',
      'Persistent monitoring of recovery conditions and thresholds',
      { inspector: false, trustLens: false, actionBar: false }),
  def('outputs', 'Recovery Reports', IconOutputs, 'Automation',
      'Generated recovery summaries and operational reports',
      { inspector: false, trustLens: false, actionBar: false }),
  def('agents', 'Recovery Agents', IconAgents, 'Automation',
      'Automated recovery workflows — stage, progress, and outcomes',
      { inspector: false, trustLens: false, actionBar: false }),

  // -- Analytics ------------------------------------------------------------
  def('compare', 'Recovery Analytics', IconCompare, 'Analytics',
      'Baseline vs AI-assisted recovery performance',
      { trustLens: false }),
  def('timeline', 'Recovery Timeline', IconTimeline, 'Analytics',
      'Transaction lifecycle from failure through outcome'),

  // -- Governance -----------------------------------------------------------
  def('audit', 'Audit Trail', IconClaims, 'Governance',
      'Every recovery decision — what happened, why, and what was recovered',
      { inspector: false, trustLens: false, actionBar: false }),
  def('policies', 'Policies', IconSettings, 'Governance',
      'Deterministic safety controls that authorize recovery execution',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
  def('model-quality', 'Model Quality', IconCompare, 'Governance',
      'AI detection precision, recall, and benchmark quality',
      { inspector: false, trustLens: false, actionBar: false }),

  // -- foot -----------------------------------------------------------------
  def('settings', 'Settings', IconSettings, 'foot',
      'Revora settings — recovery controls, appearance, and account',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),

  // -- hidden (routes preserved, not shown in navigation) -------------------
  def('challenge', 'Challenge', IconChallenge, 'hidden',
      'Stress-test an idea',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
  def('map', 'Recovery geography', IconMap, 'hidden',
      'Unavailable in the revenue recovery workspace',
      { inspector: false, trustLens: false, novaBar: false }),
  def('intel', 'Intel', IconIntel, 'hidden', 'Ranked signal',
      { trustLens: false }),
  def('news', 'Recovery signals', IconNews, 'hidden', 'Recovery signals',
      { trustLens: false }),
  def('relationships', 'Recovery relationships', IconRelationships, 'hidden', 'Recovery relationships',
      { trustLens: false }),
  def('situation', 'Recovery status', IconSituation, 'hidden', 'Recovery status',
      { inspector: false, trustLens: false, actionBar: false }),
  def('analysis', 'Analysis', IconAnalysis, 'hidden', 'Assessment',
      { inspector: false, trustLens: false, actionBar: false }),
  def('ask', 'Query', IconQuery, 'hidden', 'Semantic search',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
  def('terra-agents', 'Analyst Runs', IconRuns, 'hidden', 'Job queue',
      { inspector: false, trustLens: false, actionBar: false }),
  def('helix', 'Recovery research', IconHelix, 'hidden', 'Recovery research',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
]

const BY_ID = new Map(VIEWS.map((v) => [v.id, v]))

export function viewDef(id: ViewId): ViewDef {
  return BY_ID.get(id) ?? BY_ID.get('recovery')!
}

export const GROUPS: readonly { label: Exclude<NavGroup, 'foot' | 'hidden'>; defaultOpen: boolean }[] = [
  { label: 'Overview', defaultOpen: true },
  { label: 'Intelligence', defaultOpen: true },
  { label: 'Automation', defaultOpen: false },
  { label: 'Analytics', defaultOpen: false },
  { label: 'Governance', defaultOpen: false },
]

export const GROUP_ORDER = GROUPS.map((g) => g.label)

export function groupDefaultOpen(label: string): boolean {
  return GROUPS.find((g) => g.label === label)?.defaultOpen ?? true
}

export function groupOpen(
  label: string, view: ViewId, overrides: Record<string, boolean>,
): boolean {
  if (label === 'hidden' || label === 'foot') return false
  return overrides[label] ?? (viewDef(view).group === label || groupDefaultOpen(label))
}

export function viewsInGroup(group: NavGroup): ViewDef[] {
  return VIEWS.filter((v) => v.group === group)
}

export function isNavVisible(view: ViewDef): boolean {
  return view.group !== 'hidden' && view.group !== 'foot'
}
