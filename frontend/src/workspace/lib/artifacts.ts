// Artifacts: substantial things a reply produces, lifted out of the transcript.
//
// A 200-line component or a full HTML page pasted into a chat scroll is close
// to unusable — you scroll past the answer to reach it, you cannot see it
// running, and copying it means selecting across a page boundary. An artifact
// is the same content given its own surface: a card in the reply that opens a
// dockable panel with a live preview, the source, copy and download.
//
// What qualifies is deliberately narrow. Promoting every fenced block would put
// a card around a three-line shell snippet, which is worse than the snippet.
// The bar is "this is a deliverable, not an illustration":
//
//   * html / svg          — always, because these have a RENDERED form that the
//                           transcript cannot show at all.
//   * markdown / document — always, for the same reason (a nested document).
//   * any other language  — only past MIN_CODE_LINES, where scrolling starts to
//                           cost more than the detour to a panel.
//
// The id is derived from the message id and the block's ordinal rather than
// generated, so a re-render — or a stream that reparses the same text on every
// delta — keeps pointing at the same artifact instead of tearing the open panel
// down and rebuilding it under the reader.

export type ArtifactKind = 'html' | 'svg' | 'code' | 'doc'

export interface Artifact {
  id: string
  kind: ArtifactKind
  /** The fence's info string, e.g. `python`. Empty for an unlabelled fence. */
  lang: string
  title: string
  code: string
}

const MIN_CODE_LINES = 18

const DOC_LANGS = new Set(['markdown', 'md', 'document', 'doc'])
const HTML_LANGS = new Set(['html', 'htm'])

/** Languages that are markup/config rather than a program. They only become
 *  artifacts when they are long, and they never claim a "preview". */
const PRETTY_NAME: Record<string, string> = {
  js: 'JavaScript', jsx: 'JavaScript', ts: 'TypeScript', tsx: 'TypeScript',
  py: 'Python', python: 'Python', rs: 'Rust', rust: 'Rust', go: 'Go',
  java: 'Java', c: 'C', cpp: 'C++', cs: 'C#', rb: 'Ruby', php: 'PHP',
  sh: 'Shell', bash: 'Shell', zsh: 'Shell', ps1: 'PowerShell',
  sql: 'SQL', json: 'JSON', yaml: 'YAML', yml: 'YAML', toml: 'TOML',
  css: 'CSS', scss: 'CSS', html: 'HTML', svg: 'SVG', xml: 'XML',
  markdown: 'Document', md: 'Document',
}

export function classify(lang: string, code: string): ArtifactKind | null {
  const l = (lang || '').toLowerCase().trim()
  if (HTML_LANGS.has(l)) return 'html'
  if (l === 'svg') return 'svg'
  if (DOC_LANGS.has(l)) return 'doc'
  // An unlabelled fence that is plainly a full HTML document still gets a
  // preview — models omit the info string often enough that keying purely off
  // it would drop the most useful case.
  if (!l && /^\s*<!doctype html|^\s*<html[\s>]/i.test(code)) return 'html'
  if (!l && /^\s*<svg[\s>]/i.test(code)) return 'svg'
  if (code.split('\n').length >= MIN_CODE_LINES) return 'code'
  return null
}

/** A human title for the artifact.
 *
 *  Preference order: an explicit `title:` / filename on the fence info string,
 *  then a leading comment, then the language. Models label fences as
 *  ```python:app.py or ```html title="Dashboard" often enough to be worth
 *  reading, and a real filename beats "Python" every time. */
export function titleFor(lang: string, code: string, kind: ArtifactKind): string {
  const info = (lang || '').trim()

  const quoted = /title\s*=\s*["']([^"']+)["']/i.exec(info)
  if (quoted) return quoted[1]

  // ```python:app.py  or  ```ts app.ts
  const named = /(?:^|[:\s])([\w.-]+\.[A-Za-z][\w]{0,7})\s*$/.exec(info)
  if (named) return named[1]

  const first = code.split('\n').find((l) => l.trim())?.trim() ?? ''
  // A leading comment is usually the file's own description.
  const comment = /^(?:\/\/|#|<!--|\/\*)\s*(.{3,60}?)\s*(?:-->|\*\/)?$/.exec(first)
  if (comment && !/^[-=*_]{3,}$/.test(comment[1])) return comment[1]

  // A markdown document titles itself with its own H1.
  if (kind === 'doc') {
    const h1 = /^#\s+(.{1,70})$/m.exec(code)
    if (h1) return h1[1].trim()
  }
  if (kind === 'html') {
    const t = /<title[^>]*>([^<]{1,70})<\/title>/i.exec(code)
    if (t) return t[1].trim()
  }

  const base = info.split(/[\s:]/)[0].toLowerCase()
  return PRETTY_NAME[base] ?? (base ? base.toUpperCase() : 'Snippet')
}

export function makeArtifact(
  messageId: string, ordinal: number, lang: string, code: string,
): Artifact | null {
  const kind = classify(lang, code)
  if (!kind) return null
  return {
    id: `${messageId}:${ordinal}`,
    kind,
    lang: (lang || '').split(/[\s:]/)[0].toLowerCase(),
    title: titleFor(lang, code, kind),
    code,
  }
}

/** The file name a download should use. */
export function fileNameFor(a: Artifact): string {
  // A title that is already a filename is used as-is; otherwise it is slugged
  // and given an extension from the language.
  if (/^[\w.-]+\.[A-Za-z]\w{0,7}$/.test(a.title)) return a.title
  const ext = a.kind === 'html' ? 'html'
    : a.kind === 'svg' ? 'svg'
    : a.kind === 'doc' ? 'md'
    : ({ js: 'js', jsx: 'jsx', ts: 'ts', tsx: 'tsx', python: 'py', py: 'py',
         rust: 'rs', rs: 'rs', go: 'go', java: 'java', c: 'c', cpp: 'cpp',
         cs: 'cs', rb: 'rb', php: 'php', sh: 'sh', bash: 'sh', ps1: 'ps1',
         sql: 'sql', json: 'json', yaml: 'yaml', yml: 'yml', toml: 'toml',
         css: 'css' } as Record<string, string>)[a.lang] ?? 'txt'
  const slug = a.title.toLowerCase().replace(/[^\w]+/g, '-').replace(/^-|-$/g, '')
  return `${slug || 'artifact'}.${ext}`
}

/** Whether this artifact has something to show besides its source. */
export const isPreviewable = (a: Artifact) =>
  a.kind === 'html' || a.kind === 'svg' || a.kind === 'doc'

/** The document handed to the preview iframe.
 *
 *  SVG is wrapped so it centres and scales instead of sitting at its intrinsic
 *  size in the corner. HTML is passed through untouched — the point of an HTML
 *  artifact is to see exactly what the model wrote, and injecting a stylesheet
 *  would make the preview a lie about the source next to it.
 *
 *  The iframe this feeds is sandboxed with `allow-scripts` and WITHOUT
 *  `allow-same-origin`. That combination is the whole safety story: the frame
 *  gets a unique opaque origin, so scripts in model-authored markup can run
 *  (which is what makes an interactive artifact worth having) but cannot reach
 *  this document, its cookies, or the session behind them. Adding
 *  `allow-same-origin` alongside `allow-scripts` would let the frame remove its
 *  own sandbox attribute, which is the same as having no sandbox at all. */
export function previewDoc(a: Artifact): string {
  if (a.kind === 'svg') {
    return `<!doctype html><meta charset="utf-8">`
      + `<style>html,body{margin:0;height:100%;display:grid;place-items:center;`
      + `background:transparent}svg{max-width:100%;max-height:100%}</style>`
      + a.code
  }
  return a.code
}
