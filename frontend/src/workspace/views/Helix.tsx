// HELIX — the bioinformatics surface.
//
// The layout follows what the backend actually does. Retrieval is free and
// instant, generation is not, so papers appear as the user types and prose only
// arrives when they ask for it. Sources render BEFORE the answer and stay on
// screen beside it: the reader can check a claim while it is still being
// written, which is the whole argument for grounding it in the first place.
//
// Two panes, not a chat log. A chat transcript is the wrong shape for this —
// it buries the papers under the prose, and the papers are the evidence.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Markdown } from '../components/Markdown'
import { IconHelix, IconSources } from '../components/Icons'
import './helix.css'

interface Paper {
  pmid: string
  doi: string
  title: string
  journal: string
  year: string
  authors: string[]
  topics: string[]
  score: number
  snippet?: string
  url: string
  n?: number
}

interface TopicDef {
  key: string
  label: string
  summary: string
  methods: string[]
  tools: string[]
}

interface Status {
  ok: boolean
  ready?: boolean
  papers?: number | null
  error?: string
  hint?: string
  byTopic?: Record<string, number>
  byYear?: Record<string, number>
  journals?: Record<string, number>
  vocabulary?: number
}

const EXAMPLES = [
  'How do current methods correct batch effects in single-cell data?',
  'Is minimap2 or BWA better for long reads?',
  'What is spatial transcriptomics?',
  'Which tools should I use for metagenomics?',
  'How accurate is AlphaFold on protein complexes?',
  'Do polygenic scores transfer across ancestries?',
]

export function HelixView() {
  const [status, setStatus] = useState<Status | null>(null)
  const [topics, setTopics] = useState<TopicDef[]>([])
  const [topic, setTopic] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [papers, setPapers] = useState<Paper[]>([])
  const [searchMs, setSearchMs] = useState<number | null>(null)

  const [answer, setAnswer] = useState('')
  const [cited, setCited] = useState<Paper[]>([])
  const [busy, setBusy] = useState(false)
  const [deep, setDeep] = useState(false)
  const [meta, setMeta] = useState<{ model?: string; kind?: string } | null>(null)
  const [timing, setTiming] = useState<{ first?: number; total?: number }>({})
  const [error, setError] = useState('')

  const abort = useRef<AbortController | null>(null)
  const answerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    fetch('/api/helix/status').then((r) => r.json())
      .then((d) => { if (live) setStatus(d) })
      .catch(() => { if (live) setStatus({ ok: false, error: 'HELIX is unreachable' }) })
    fetch('/api/helix/topics').then((r) => r.json())
      .then((d) => { if (live) setTopics(d.topics || []) })
      .catch(() => { /* the rail degrades to no topic filter */ })
    return () => { live = false }
  }, [])

  // Papers as you type. Debounced, and guarded so a slow reply for an earlier
  // prefix cannot overwrite results for what is now in the box.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 3) { setPapers([]); setSearchMs(null); return }
    let live = true
    const t = setTimeout(() => {
      const params = new URLSearchParams({ q, limit: '12' })
      if (topic) params.set('topic', topic)
      fetch(`/api/helix/search?${params}`)
        .then((r) => r.json())
        .then((d) => {
          if (!live) return
          setPapers(d.results || [])
          setSearchMs(d.tookMs ?? null)
        })
        .catch(() => { if (live) setPapers([]) })
    }, 180)
    return () => { live = false; clearTimeout(t) }
  }, [query, topic])

  const ask = useCallback(async (q: string) => {
    const question = q.trim()
    if (!question || busy) return
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl

    setBusy(true); setAnswer(''); setCited([]); setError(''); setMeta(null)
    setTiming({})
    const started = performance.now()
    let first: number | undefined

    try {
      const res = await fetch('/api/helix/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, deep, topic }),
        signal: ctl.signal,
      })
      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error || `HTTP ${res.status}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let event = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE frames are separated by a blank line; anything after the last
        // one is a partial frame and has to wait for the next read.
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''
        for (const frame of frames) {
          for (const line of frame.split('\n')) {
            if (line.startsWith('event: ')) event = line.slice(7).trim()
            else if (line.startsWith('data: ')) {
              const data = JSON.parse(line.slice(6))
              if (event === 'meta') setMeta({ model: data.model, kind: data.kind })
              else if (event === 'sources') setCited(data.sources || [])
              else if (event === 'delta') {
                if (first === undefined) {
                  first = performance.now() - started
                  setTiming((t) => ({ ...t, first }))
                }
                setAnswer((a) => a + data.text)
              } else if (event === 'error') setError(data.message)
              else if (event === 'done') {
                setTiming((t) => ({ ...t, total: performance.now() - started }))
              }
            }
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }, [busy, deep, topic])

  // Cancel an in-flight answer if the view goes away.
  useEffect(() => () => abort.current?.abort(), [])

  useEffect(() => {
    answerRef.current?.scrollTo({ top: answerRef.current.scrollHeight })
  }, [answer])

  const activeTopic = useMemo(
    () => topics.find((t) => t.key === topic) || null, [topics, topic])

  const submit = (e: React.FormEvent) => { e.preventDefault(); void ask(query) }

  if (status && !status.ok) {
    return (
      <div className="omx-scroll omx-helix">
        <div className="omx-helix-empty">
          <IconHelix size={34} />
          <h2>The bioinformatics corpus is not built yet</h2>
          <p>{status.error}</p>
          <pre>python -m omnix.helix.ingest</pre>
        </div>
      </div>
    )
  }

  return (
    <div className="omx-helix">
      {/* -- header ------------------------------------------------------- */}
      <header className="omx-helix-head">
        <div className="omx-helix-title">
          <IconHelix size={18} />
          <h1>Bioinformatics</h1>
          <span className="omx-helix-count">
            {status?.papers
              ? `${status.papers.toLocaleString()} papers · PubMed`
              : 'index builds on your first question'}
          </span>
        </div>

        <form className="omx-helix-ask" onSubmit={submit}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask the bioinformatics literature…"
            aria-label="Ask the bioinformatics literature"
            autoFocus
          />
          <label className="omx-helix-deep" title="Read twice as many papers with the larger model">
            <input type="checkbox" checked={deep}
                   onChange={(e) => setDeep(e.target.checked)} />
            Deep
          </label>
          <button type="submit" disabled={busy || !query.trim()}>
            {busy ? 'Reading…' : 'Ask'}
          </button>
        </form>

        <div className="omx-helix-topics">
          <button className={topic === null ? 'on' : ''}
                  onClick={() => setTopic(null)}>All</button>
          {topics.map((t) => (
            <button key={t.key} className={topic === t.key ? 'on' : ''}
                    onClick={() => setTopic(topic === t.key ? null : t.key)}
                    title={t.summary}>
              {t.label}
              {status?.byTopic?.[t.key]
                ? <span className="omx-helix-badge">{status.byTopic[t.key]}</span>
                : null}
            </button>
          ))}
        </div>
      </header>

      {/* -- body --------------------------------------------------------- */}
      <div className="omx-helix-body">
        {/* answer */}
        <section className="omx-helix-answer" ref={answerRef}>
          {!answer && !busy && !error && (
            <div className="omx-helix-intro">
              {activeTopic ? (
                <>
                  <h3>{activeTopic.label}</h3>
                  <p>{activeTopic.summary}</p>
                  <h4>Core methods</h4>
                  <ul>{activeTopic.methods.map((m) => <li key={m}>{m}</li>)}</ul>
                  <h4>Tools</h4>
                  <p className="omx-helix-tools">
                    {activeTopic.tools.map((t) => (
                      <code key={t} onClick={() => setQuery(t)}>{t}</code>
                    ))}
                  </p>
                </>
              ) : (
                <>
                  <h3>Ask the literature, not a model's memory</h3>
                  <p>
                    Every answer is written from papers retrieved out of a local
                    PubMed corpus and cites them by number. When the corpus does
                    not cover something, it says so rather than guessing.
                  </p>
                  <div className="omx-helix-examples">
                    {EXAMPLES.map((ex) => (
                      <button key={ex} onClick={() => { setQuery(ex); void ask(ex) }}>
                        {ex}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {(answer || busy) && (
            <>
              <div className="omx-helix-meta">
                {meta?.model && <span>{meta.model.split('/').pop()}</span>}
                {meta?.kind === 'definition' || meta?.kind === 'tools'
                  ? <span className="omx-helix-instant">no model needed</span>
                  : null}
                {timing.first !== undefined &&
                  <span>first word {(timing.first / 1000).toFixed(2)}s</span>}
                {timing.total !== undefined &&
                  <span>done {(timing.total / 1000).toFixed(2)}s</span>}
              </div>
              {answer
                ? <Markdown text={answer} />
                : <p className="omx-helix-waiting">Reading the papers…</p>}
            </>
          )}

          {error && <div className="omx-helix-error">{error}</div>}

          {cited.length > 0 && (
            <div className="omx-helix-cited">
              <h4>Cited</h4>
              <ol>
                {cited.map((s) => (
                  <li key={s.pmid} id={`helix-src-${s.n}`}>
                    <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
                    <span className="omx-helix-where">
                      {s.authors?.[0] ? `${s.authors[0]}${s.authors.length > 1 ? ' et al.' : ''} — ` : ''}
                      {s.journal} {s.year} · PMID {s.pmid}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </section>

        {/* papers */}
        <aside className="omx-helix-papers">
          <div className="omx-helix-papers-head">
            <IconSources size={14} />
            <span>{papers.length ? `${papers.length} papers` : 'Papers'}</span>
            {searchMs !== null && (
              <span className="omx-helix-ms">{searchMs.toFixed(1)}ms</span>
            )}
          </div>
          {papers.length === 0 ? (
            <p className="omx-helix-hint">
              {query.trim().length < 3
                ? 'Type to search the corpus. Results appear as you type — no model runs.'
                : 'Nothing matches. Try naming a method, a tool or an assay.'}
            </p>
          ) : (
            <ul>
              {papers.map((p) => (
                <li key={p.pmid}>
                  <a href={p.url} target="_blank" rel="noreferrer">{p.title}</a>
                  <div className="omx-helix-where">
                    {p.journal} {p.year} · PMID {p.pmid}
                  </div>
                  {p.snippet && <p className="omx-helix-snip">{p.snippet}…</p>}
                  <div className="omx-helix-tags">
                    {p.topics.map((t) => <span key={t}>{t}</span>)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  )
}
