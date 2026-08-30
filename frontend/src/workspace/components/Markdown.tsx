// A small markdown renderer for model output.
//
// Every model in this product writes markdown — NOVA's answers, ARENA's
// verdict, each seat's argument, ORACLE's synthesis — and all of it was being
// printed literally. A judgment that opens with `**Short answer:**` and lays
// its comparison out in `| pipe | tables |` is not a formatting nitpick: the
// user is reading source rather than prose, and it makes a good answer look
// like a broken one.
//
// This builds React elements rather than setting innerHTML. Model output is
// untrusted text and the one thing a renderer must never do is hand it to the
// HTML parser — no `dangerouslySetInnerHTML` anywhere in this file, so there
// is no injection surface to reason about.
//
// Scope is deliberately what models actually emit: headings, bold, italic,
// inline code, fenced code, bullet and numbered lists, pipe tables, block
// quotes, horizontal rules, links and paragraphs. Anything unrecognised falls
// through as plain text rather than disappearing.

import { memo, useState, type ReactNode } from 'react'
import { IconCheck, IconCopy } from './Icons'
import { Chart, parseChart } from './Chart'
import { ArtifactCard } from './Artifact'
import { makeArtifact, type Artifact } from '../lib/artifacts'

/** Turns fenced blocks into richer things than a code listing.
 *
 *  Optional, and off everywhere except the chat transcript: ARENA's seats,
 *  ORACLE's synthesis and the Inspector all render markdown too, and a 400-line
 *  artifact panel launching out of a debate seat is not what those surfaces
 *  are for. */
export interface MarkdownEnrich {
  /** Namespaces artifact ids so two messages holding the same code do not
   *  collide in the open-panel check. */
  messageId: string
  /** The artifact currently open in the panel, if any. */
  openId?: string | null
  onOpen: (a: Artifact) => void
}

/** A fenced code block with its language and a copy button.
 *
 *  Its own component because it holds state — the two seconds of "Copied"
 *  after a click. Without that acknowledgement a copy button is indistinguish-
 *  able from a dead one: the clipboard is invisible, so the only evidence the
 *  press did anything is the label changing.
 *
 *  `navigator.clipboard` is unavailable on a non-secure origin that is not
 *  localhost, and rejects if the document is not focused. Both are silent
 *  failures, so the catch flips the label to a failure state rather than
 *  leaving the button claiming success. */
function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'bad'>('idle')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setState('ok')
    } catch {
      setState('bad')
    }
    setTimeout(() => setState('idle'), 1800)
  }

  return (
    <div className="omx-md-code">
      <div className="omx-md-codehead">
        <span className="lang">{lang || 'text'}</span>
        <button className="cp" onClick={() => void copy()} type="button"
                title="Copy code">
          {state === 'ok' ? <IconCheck size={12} /> : <IconCopy size={12} />}
          {state === 'ok' ? 'Copied' : state === 'bad' ? 'Blocked' : 'Copy'}
        </button>
      </div>
      <pre className="omx-md-pre"><code>{code}</code></pre>
    </div>
  )
}

/** Inline formatting, applied within a single line of text. */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  // One pass, longest-delimiter-first so `**` is not eaten by `*`.
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*]+\*)|(_[^_]+_)|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyBase}-i${i++}`
    if (tok.startsWith('`')) {
      out.push(<code key={key}>{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**') || tok.startsWith('__')) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('[')) {
      const cut = tok.indexOf('](')
      const label = tok.slice(1, cut)
      const href = tok.slice(cut + 2, -1)
      // Only http(s) is linkable. `javascript:` in a model-authored link is
      // the one genuinely dangerous thing markdown can carry.
      const safe = /^https?:\/\//i.test(href)
      out.push(safe
        ? <a key={key} href={href} target="_blank" rel="noreferrer noopener">{label}</a>
        : <span key={key}>{label}</span>)
    } else {
      out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    }
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out.length ? out : [text]
}

const isTableRow = (l: string) => l.trim().startsWith('|') && l.trim().endsWith('|')
const isDivider = (l: string) => /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(l) && l.includes('-')
const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())

export interface MarkdownProps {
  text: string
  className?: string
  enrich?: MarkdownEnrich
  /** Adds the class that animates newly-arrived blocks in. Presentational
   *  only — it changes no parsing. */
  streaming?: boolean
}

function MarkdownInner({ text, className, enrich, streaming }: MarkdownProps) {
  const lines = (text || '').replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let k = 0
  // Counts EVERY fence, including ones that stay plain code blocks. Keying the
  // artifact id off a count of artifacts-so-far would renumber every later
  // artifact the moment an earlier block grew past the promotion threshold
  // mid-stream, closing whatever the reader had open.
  let fence = 0

  const flushPara = () => {
    if (!para.length) return
    const body = para.join(' ')
    blocks.push(<p key={`p${k++}`}>{inline(body, `p${k}`)}</p>)
    para = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()

    // fenced code. The info string after the fence is the language, which is
    // what the block's header labels — models emit it consistently and it is
    // the fastest way for a reader to tell a shell snippet from a Python one.
    if (trimmed.startsWith('```')) {
      flushPara()
      // The full info string is kept as well as the bare language: an artifact
      // reads `title="…"` and `python:app.py` out of it, and only the first
      // token is a language.
      const info = trimmed.slice(3).trim()
      const lang = info.split(/\s+/)[0] || ''
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) buf.push(lines[i++])
      // Whether the fence was actually CLOSED. While a reply streams, the last
      // fence is open and its body is a prefix of the real one — promoting that
      // to an artifact would pop a panel open around half a file and then
      // rebuild it on every token.
      const closed = i < lines.length
      const code = buf.join('\n')
      const ord = fence++

      // A chart is a rendered figure, not a listing, so it replaces the block
      // outright. A spec that does not parse falls through to the code path
      // below rather than vanishing — seeing the JSON beats seeing nothing.
      if (closed && (lang === 'chart' || lang === 'graph')) {
        const spec = parseChart(code)
        if (spec) {
          blocks.push(<Chart key={`g${k++}`} spec={spec} />)
          continue
        }
      }

      if (enrich && closed) {
        const art = makeArtifact(enrich.messageId, ord, info, code)
        if (art) {
          blocks.push(
            <ArtifactCard
              key={`a${k++}`} a={art}
              open={enrich.openId === art.id}
              onOpen={() => enrich.onOpen(art)}
            />,
          )
          continue
        }
      }

      blocks.push(<CodeBlock key={`c${k++}`} code={code} lang={lang} />)
      continue
    }

    if (!trimmed) { flushPara(); continue }

    // horizontal rule
    if (/^([-*_])\1{2,}$/.test(trimmed)) {
      flushPara()
      blocks.push(<hr key={`h${k++}`} />)
      continue
    }

    // heading
    const head = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (head) {
      flushPara()
      // Model headings are section labels inside an answer, not page titles —
      // rendering an h1 at 30px inside a card is louder than anything around
      // it, so the scale is shifted down and floored at h3.
      //
      // `#` and `##` deliberately land on the SAME level. The shared style
      // contract asks every agent for `##` section headings, and the old
      // straight two-level shift put those at h4 — a hair above body text, so
      // a properly structured answer read as one undifferentiated block and
      // the structure the model had gone to the trouble of producing was
      // invisible. Whichever of the two a model reaches for now gets the
      // prominent size, and only genuinely nested headings step down.
      const depth = Math.min(6, Math.max(3, head[1].length + 1))
      const Tag = `h${depth}` as 'h3'
      blocks.push(<Tag key={`h${k++}`} className="omx-md-h">{inline(head[2], `h${k}`)}</Tag>)
      continue
    }

    // table
    if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      flushPara()
      const header = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]))
      i--
      blocks.push(
        <div key={`t${k++}`} className="omx-md-tablewrap">
          <table className="omx-md-table">
            <thead><tr>{header.map((h, x) => <th key={x}>{inline(h, `th${x}`)}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, y) => (
                <tr key={y}>{r.map((c, x) => <td key={x}>{inline(c, `td${y}-${x}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // block quote
    if (trimmed.startsWith('>')) {
      flushPara()
      const buf: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      i--
      blocks.push(
        <blockquote key={`q${k++}`} className="omx-md-quote">
          {inline(buf.join(' '), `q${k}`)}
        </blockquote>,
      )
      continue
    }

    // lists — bullet or ordered, kept together until the run ends
    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed)
    const ordered = /^\d+[.)]\s+(.*)$/.exec(trimmed)
    if (bullet || ordered) {
      flushPara()
      const isOrdered = !!ordered
      const items: string[] = []
      while (i < lines.length) {
        const t = lines[i].trim()
        const b = /^[-*+]\s+(.*)$/.exec(t)
        const o = /^\d+[.)]\s+(.*)$/.exec(t)
        if (isOrdered ? o : b) { items.push((isOrdered ? o! : b!)[1]); i++; continue }
        // A wrapped continuation line belongs to the item above it.
        if (t && !/^([-*+]|\d+[.)])\s/.test(t) && items.length) { items[items.length - 1] += ' ' + t; i++; continue }
        break
      }
      i--
      const Tag = isOrdered ? 'ol' : 'ul'
      blocks.push(
        <Tag key={`l${k++}`} className="omx-md-list">
          {items.map((it, x) => <li key={x}>{inline(it, `li${x}`)}</li>)}
        </Tag>,
      )
      continue
    }

    para.push(trimmed)
  }
  flushPara()

  return (
    <div className={`omx-md ${streaming ? 'omx-md-live' : ''} ${className ?? ''}`}>
      {blocks}
    </div>
  )
}

/** Memoised, and this matters more than it looks.
 *
 *  The smooth reveal repaints the streaming message on every animation frame.
 *  Without memoisation React re-parses and rebuilds EVERY message in the
 *  transcript 60 times a second, so a long conversation degrades until the
 *  stream visibly stutters — the exact problem the reveal exists to fix.
 *
 *  The comparator is explicit because `enrich` is an object literal built
 *  during the parent's render and would fail a reference check every time,
 *  making the memo a no-op. Only the fields that actually change what is
 *  rendered are compared; `onOpen` is a stable `useState` setter. */
export const Markdown = memo(MarkdownInner, (a, b) =>
  a.text === b.text
  && a.className === b.className
  && a.streaming === b.streaming
  && a.enrich?.messageId === b.enrich?.messageId
  && a.enrich?.openId === b.enrich?.openId)
