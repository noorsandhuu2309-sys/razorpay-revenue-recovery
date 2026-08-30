// Find-in-graph matching.
//
// Its own module rather than a helper inside the panel: it is pure, it is the
// part worth testing, and exporting a function from a component file costs
// React Fast Refresh for that file.

import type { GraphModel } from './graphModel'

export interface FindHit { id: string; name: string; rank: number }

/** Case-insensitive substring, ranked so the most useful answer is first.
 *
 *  Prefix beats contains, then shorter names beat longer ones: typing "iran"
 *  should land on Iran rather than on "Sanctions on Iran", and the whole point
 *  is to be right on the first row so the reader never reads the second.
 *
 *  An empty query returns NOTHING rather than everything. The canvas dims
 *  non-matches, so "no query" and "everything matches" have to be
 *  distinguishable or an empty box would grey out the entire graph. */
export function findMatches(model: GraphModel, query: string, limit = 12): FindHit[] {
  const q = query.trim().toLowerCase()
  if (q.length < 1) return []
  const hits: FindHit[] = []
  for (const n of model.nodes) {
    const name = n.o.name
    const at = name.toLowerCase().indexOf(q)
    if (at === -1) continue
    hits.push({ id: n.id, name, rank: (at === 0 ? 0 : 1000) + at * 10 + name.length })
  }
  hits.sort((a, b) => a.rank - b.rank)
  return hits.slice(0, limit)
}
