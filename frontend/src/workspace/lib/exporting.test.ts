import { describe, expect, it } from 'vitest'
import { asPlainText, tablesAsTsv, titleFrom } from './exporting'

describe('asPlainText', () => {
  it('drops syntax but keeps the shape', () => {
    const out = asPlainText('## Heading\n\n- **one**\n- *two*\n')
    expect(out).toContain('Heading')
    expect(out).toContain('• one')
    expect(out).toContain('• two')
    expect(out).not.toContain('**')
    expect(out).not.toContain('##')
  })

  it('keeps a link readable by carrying the URL', () => {
    // Somebody pasting into an email loses the reference entirely if the href
    // is dropped along with the syntax.
    expect(asPlainText('See [the docs](https://x.com/a).'))
      .toBe('See the docs (https://x.com/a).')
  })

  it('unwraps code without mangling it', () => {
    expect(asPlainText('Run `npm test` first.')).toBe('Run npm test first.')
    expect(asPlainText('```py\nx = 1\n```')).toBe('x = 1')
  })

  it('does not eat a bare asterisk used as multiplication', () => {
    expect(asPlainText('3 * 4 = 12')).toBe('3 * 4 = 12')
  })

  it('collapses runs of blank lines', () => {
    expect(asPlainText('a\n\n\n\n\nb')).toBe('a\n\nb')
  })
})

describe('tablesAsTsv', () => {
  const MD = `Intro text.

| Country | Share |
|---------|-------|
| Chile   | 36%   |
| Australia | 24% |

Trailing text.`

  it('extracts a table as tab-separated rows', () => {
    const tsv = tablesAsTsv(MD)
    expect(tsv).toBe('Country\tShare\nChile\t36%\nAustralia\t24%')
  })

  it('returns null when there is no table', () => {
    expect(tablesAsTsv('Just a paragraph.')).toBeNull()
    // A pipe in prose is not a table without the divider row under it.
    expect(tablesAsTsv('| not | a table |')).toBeNull()
  })

  it('handles more than one table', () => {
    const two = `${MD}\n\n| A | B |\n|---|---|\n| 1 | 2 |\n`
    const tsv = tablesAsTsv(two) ?? ''
    expect(tsv).toContain('Country\tShare')
    expect(tsv).toContain('A\tB')
    expect(tsv).toContain('1\t2')
  })
})

describe('titleFrom', () => {
  it('prefers the answer’s own first heading', () => {
    expect(titleFrom('## Lithium reserves\n\nSome text.'))
      .toBe('Lithium reserves')
  })

  it('strips formatting out of that heading', () => {
    expect(titleFrom('# **Bold** title')).toBe('Bold title')
  })

  it('falls back to the first sentence', () => {
    expect(titleFrom('Chile holds the largest reserves. More text here.'))
      .toBe('Chile holds the largest reserves.')
  })

  it('truncates a very long first sentence', () => {
    const long = `${'word '.repeat(40)}.`
    const t = titleFrom(long)
    expect(t.length).toBeLessThanOrEqual(70)
    expect(t.endsWith('…')).toBe(true)
  })

  it('uses the fallback for empty input', () => {
    expect(titleFrom('', 'Untitled')).toBe('Untitled')
    expect(titleFrom('```\ncode only\n```', 'Untitled')).toBe('Untitled')
  })
})
