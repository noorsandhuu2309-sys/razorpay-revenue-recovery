import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ACCENTS, apply, current, hydrate, inkNow, setAccent, setMode, stored,
  toggleMode,
} from './appearance'

const root = () => document.documentElement
const attrs = () => ({
  theme: root().getAttribute('data-theme'),
  accent: root().getAttribute('data-accent'),
})

beforeEach(() => {
  localStorage.clear()
  root().removeAttribute('data-theme')
  root().removeAttribute('data-accent')
  root().classList.remove('omx-swapping')
  vi.useRealTimers()
})

describe('the two axes', () => {
  it('defaults to dark gold', () => {
    expect(current()).toEqual({ mode: 'dark', accent: 'gold' })
  })

  it('moves each axis without disturbing the other', () => {
    apply({ mode: 'dark', accent: 'blue' })
    expect(attrs()).toEqual({ theme: 'dark', accent: 'blue' })

    setMode('light')
    // The accent survives the mode change. It is the bug this model exists to
    // prevent: a single `theme` string would have made "light" and "blue"
    // mutually exclusive.
    expect(current()).toEqual({ mode: 'light', accent: 'blue' })

    setAccent('mono')
    expect(current()).toEqual({ mode: 'light', accent: 'mono' })
  })

  it('round-trips both axes through storage', () => {
    apply({ mode: 'light', accent: 'green' })
    expect(stored()).toEqual({ mode: 'light', accent: 'green' })
  })

  it('keeps the legacy omx-theme key so an existing choice survives', () => {
    localStorage.setItem('omx-theme', 'light')
    expect(stored().mode).toBe('light')
  })

  it('falls back to gold on an unknown accent rather than rendering unstyled', () => {
    localStorage.setItem('omx-accent', 'chartreuse')
    expect(stored().accent).toBe('gold')

    root().setAttribute('data-accent', 'chartreuse')
    expect(current().accent).toBe('gold')
  })

  it('hydrates <html> from storage', () => {
    localStorage.setItem('omx-theme', 'light')
    localStorage.setItem('omx-accent', 'red')
    hydrate()
    expect(attrs()).toEqual({ theme: 'light', accent: 'red' })
  })

  it('toggles the mode both ways', () => {
    hydrate()
    toggleMode()
    expect(current().mode).toBe('light')
    toggleMode()
    expect(current().mode).toBe('dark')
  })
})

describe('the cross-fade', () => {
  it('marks the swap and then unmarks it', () => {
    vi.useFakeTimers()
    hydrate()
    setAccent('red')
    expect(root().classList.contains('omx-swapping')).toBe(true)
    // Left on permanently it would also tween hover, putting a 260ms lag on
    // every button highlight in the product.
    vi.advanceTimersByTime(400)
    expect(root().classList.contains('omx-swapping')).toBe(false)
  })

  it('does nothing at all when the pair is unchanged', () => {
    hydrate()
    apply({ mode: 'dark', accent: 'gold' })
    expect(root().classList.contains('omx-swapping')).toBe(false)
  })
})

describe('canvas ink', () => {
  it('is cached per pair, so a frame loop can call it freely', () => {
    hydrate()
    expect(inkNow()).toBe(inkNow())
  })

  it('is rebuilt when either axis changes', () => {
    hydrate()
    const a = inkNow()
    setAccent('blue')
    const b = inkNow()
    expect(b).not.toBe(a)
    expect(b.accent).toBe('blue')

    setMode('light')
    const c = inkNow()
    expect(c).not.toBe(b)
    expect(c.mode).toBe('light')
  })

  it('reports the mode the map and graph key their neutrals off', () => {
    apply({ mode: 'light', accent: 'mono' })
    const ink = inkNow()
    expect(ink.mode).toBe('light')
    // Plates flip with the ground, not with the accent: a dark label plate on
    // a white canvas is the one way a canvas looks broken while every DOM
    // surface around it looks right.
    expect(ink.plate).toContain('255,254,251')
    expect(ink.strong(1)).toContain('26,23,16')
  })

  it('composes accent alphas from whatever the cascade resolved', () => {
    hydrate()
    // jsdom does not apply stylesheets, so the token read falls back — what is
    // under test is that the alpha is composed onto the resolved triplet
    // rather than onto a hard-coded gold.
    const ink = inkNow()
    expect(ink.accentAt(0.5)).toBe(`rgba(${ink.aRgb},0.5)`)
  })
})

describe('the accent list', () => {
  it('names no colours — the swatches are painted by the stylesheet', () => {
    const asText = JSON.stringify(ACCENTS)
    expect(asText).not.toMatch(/#[0-9a-fA-F]{3,6}/)
    expect(asText).not.toMatch(/rgba?\(/)
  })

  it('covers exactly the five ramps workspace.css defines', () => {
    expect(ACCENTS.map((a) => a.id))
      .toEqual(['gold', 'red', 'blue', 'green', 'mono'])
  })
})
