// Getting an answer out of OMNIX: to the clipboard, or to a file.
//
// Named `exporting` rather than `export` because `export` is a reserved word
// and `from '../lib/export'` reads like a syntax error at every call site.
//
// The document formats are rendered by the SERVER (`/api/export`, see
// omnix/tools/docgen.py). DOCX and PDF need real writers, and bundling those
// into the browser would cost megabytes for a button most sessions never press.
// Markdown and plain text are done here, because they need nothing.

export type ExportFormat = 'docx' | 'pdf' | 'html' | 'md' | 'txt'

export const FORMATS: { id: ExportFormat; label: string; hint: string }[] = [
  { id: 'docx', label: 'Word', hint: 'Editable .docx' },
  { id: 'pdf', label: 'PDF', hint: 'Fixed layout, for sending on' },
  { id: 'html', label: 'Web page', hint: 'Self-contained .html' },
  { id: 'md', label: 'Markdown', hint: 'The source' },
  { id: 'txt', label: 'Plain text', hint: 'No formatting' },
]

/** Hand a blob to the browser as a download. */
function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  // Revoked on a later tick: revoking synchronously races the download in
  // Chromium and writes a zero-byte file.
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

const slug = (s: string) =>
  (s || 'omnix').replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-')
    .toLowerCase().slice(0, 60) || 'omnix'

/**
 * Render `markdown` as `format` and download it.
 *
 * Throws with a readable message on failure — the caller shows it. A download
 * button that silently does nothing is indistinguishable from a broken one.
 */
export async function exportDocument(
  markdown: string, format: ExportFormat, title: string, subtitle = '',
): Promise<void> {
  const body = (markdown || '').trim()
  if (!body) throw new Error('There is nothing to export yet.')

  const res = await fetch('/api/export', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ markdown: body, format, title, subtitle }),
  })

  if (!res.ok) {
    let detail = `Export failed (${res.status}).`
    try { detail = (await res.json()).error || detail } catch { /* not JSON */ }
    throw new Error(detail)
  }

  // Prefer the filename the server chose; it has already been sanitised.
  const disp = res.headers.get('Content-Disposition') || ''
  const named = /filename="([^"]+)"/.exec(disp)
  save(await res.blob(), named?.[1] ?? `${slug(title)}.${format}`)
}

/** Copy text, reporting whether it landed.
 *
 *  `navigator.clipboard` is unavailable on a non-secure origin that is not
 *  localhost, and rejects when the document is not focused — both silent. The
 *  textarea path is the fallback that still works in those cases. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

/** Markdown reduced to what reads well when pasted into an email or a doc.
 *
 *  Not the same as the `txt` export: this keeps the shape (bullets, numbering,
 *  code indentation) and only drops the SYNTAX, because someone pasting into
 *  Gmail wants the list to still look like a list. */
export function asPlainText(md: string): string {
  return (md || '')
    .replace(/```[a-zA-Z0-9+-]*\n?/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '$1')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '• ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** A markdown pipe table as TSV, which is what spreadsheets paste correctly.
 *  Returns null when the text holds no table. */
export function tablesAsTsv(md: string): string | null {
  const lines = (md || '').split('\n')
  const out: string[] = []
  for (let i = 0; i < lines.length; i++) {
    const row = lines[i].trim()
    const isRow = row.startsWith('|') && row.endsWith('|')
    const divider = /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1] ?? '')
    if (!isRow || !divider) continue
    const cells = (l: string) =>
      l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
    out.push(cells(row).join('\t'))
    i += 2
    while (i < lines.length && lines[i].trim().startsWith('|')) {
      out.push(cells(lines[i]).join('\t'))
      i++
    }
    i--
    out.push('')
  }
  return out.length ? out.join('\n').trim() : null
}

/** A title for the exported file, taken from the answer's own first heading or
 *  first sentence. Asking a model for a filename costs a round trip; the answer
 *  has already told us what it is about. */
export function titleFrom(md: string, fallback = 'OMNIX answer'): string {
  const heading = /^\s{0,3}#{1,6}\s+(.{3,80})$/m.exec(md || '')
  if (heading) return heading[1].replace(/[*`_]/g, '').trim()
  const first = (md || '').replace(/```[\s\S]*?```/g, ' ')
    .replace(/[#*`>_-]/g, ' ').replace(/\s+/g, ' ').trim()
  if (!first) return fallback
  const sentence = first.split(/(?<=[.!?])\s/)[0]
  return (sentence.length > 70 ? `${sentence.slice(0, 67)}…` : sentence) || fallback
}
