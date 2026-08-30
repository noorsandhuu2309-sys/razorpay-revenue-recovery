// The voice orb: what you look at while OMNIX is listening or talking.
//
// Drawn on a canvas from the REAL amplitude envelope — the mic's peak level
// while listening, the AnalyserNode's output while speaking. That is the whole
// design decision. An animation on a sine timer is immediately recognisable as
// fake, because it keeps swelling through the gap between two words; one driven
// by the waveform settles when the voice settles, and the eye reads it as the
// machine actually hearing something.
//
// Three rings rather than one, rotating at different rates, so the shape
// breathes instead of pulsing uniformly. Each ring is a closed curve whose
// radius is modulated by a couple of low-frequency sinusoids plus the live
// level, which gives an organic wobble without any noise library.

import { useEffect, useRef } from 'react'

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking'

/** Read the live 0..1 level. A getter rather than a prop because the level
 *  changes every frame and passing it as a prop would re-render this component
 *  sixty times a second — the canvas loop reads it directly instead. */
export function VoiceOrb({ state, level, size = 132 }: {
  state: OrbState
  level: () => number
  size?: number
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  // Held in a ref so the animation loop always sees the current state without
  // being torn down and restarted on every transition.
  const stateRef = useRef(state)
  stateRef.current = state

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

    // Colours are read from the theme once per resize, not hard-coded: the
    // shell has ten theme x accent combinations and the orb has to follow all
    // of them. getComputedStyle returns a custom property's TOKEN TEXT, so the
    // accent tokens are literal hex by contract (see workspace.css's header) —
    // a color-mix() value would arrive here unparsed.
    let accent = '#d4a545'
    let dim = 'rgba(212,165,69,0.28)'

    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const resize = () => {
      canvas.width = Math.ceil(size * dpr)
      canvas.height = Math.ceil(size * dpr)
      canvas.style.width = `${size}px`
      canvas.style.height = `${size}px`
      const css = getComputedStyle(document.documentElement)
      accent = (css.getPropertyValue('--omx-accent')
        || css.getPropertyValue('--omx-gold') || '#d4a545').trim()
      dim = (css.getPropertyValue('--omx-gold-glow')
        || 'rgba(212,165,69,0.28)').trim()
    }
    resize()

    let raf = 0
    let t = 0
    // Eased separately from the audio level so a transition between states is
    // smooth even when the level jumps.
    let shown = 0

    const draw = () => {
      t += reduced ? 0 : 0.016
      const s = stateRef.current
      const raw = s === 'listening' || s === 'speaking' ? Math.min(1, level()) : 0
      // Thinking has no audio, so it gets a slow breath of its own — the one
      // place a timer is the honest source, because nothing is being heard.
      const target = s === 'thinking'
        ? 0.28 + Math.sin(t * 2.2) * 0.14
        : s === 'idle' ? 0.06 : raw
      shown += (target - shown) * 0.18

      const w = canvas.width
      const c = w / 2
      const base = w * 0.26
      ctx.clearRect(0, 0, w, w)

      // Outer halo. Scaled by level so loud speech visibly blooms.
      const halo = ctx.createRadialGradient(c, c, base * 0.5, c, c,
                                            base * (1.7 + shown * 0.9))
      halo.addColorStop(0, dim)
      halo.addColorStop(1, 'transparent')
      ctx.fillStyle = halo
      ctx.beginPath()
      ctx.arc(c, c, base * (1.7 + shown * 0.9), 0, Math.PI * 2)
      ctx.fill()

      const rings = [
        { r: 1.0, wob: 0.10, speed: 1.0, alpha: 0.95, width: 2.0 },
        { r: 1.22, wob: 0.14, speed: -0.72, alpha: 0.45, width: 1.4 },
        { r: 1.44, wob: 0.18, speed: 0.51, alpha: 0.22, width: 1.1 },
      ]

      for (const ring of rings) {
        ctx.beginPath()
        const steps = 84
        for (let i = 0; i <= steps; i++) {
          const a = (i / steps) * Math.PI * 2
          // Two out-of-phase harmonics: one alone reads as a rotating ellipse.
          const wobble =
            Math.sin(a * 3 + t * ring.speed * 1.6) * ring.wob
            + Math.sin(a * 5 - t * ring.speed) * ring.wob * 0.55
          const r = base * ring.r * (1 + wobble * (0.25 + shown * 1.15)
                                     + shown * 0.30)
          const x = c + Math.cos(a) * r
          const y = c + Math.sin(a) * r
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.closePath()
        ctx.strokeStyle = accent
        ctx.globalAlpha = ring.alpha * (s === 'idle' ? 0.5 : 1)
        ctx.lineWidth = ring.width * dpr
        ctx.stroke()
      }
      ctx.globalAlpha = 1

      // Solid core, so the orb has a centre of mass to look at.
      const core = ctx.createRadialGradient(c, c, 0, c, c, base * 0.62)
      core.addColorStop(0, accent)
      core.addColorStop(1, 'transparent')
      ctx.fillStyle = core
      ctx.globalAlpha = 0.30 + shown * 0.45
      ctx.beginPath()
      ctx.arc(c, c, base * 0.62, 0, Math.PI * 2)
      ctx.fill()
      ctx.globalAlpha = 1

      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)

    // Follows a theme or accent change without a remount.
    const obs = new MutationObserver(resize)
    obs.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme', 'data-accent'],
    })

    return () => {
      cancelAnimationFrame(raf)
      obs.disconnect()
    }
  }, [size, level])

  return (
    <canvas
      ref={ref}
      className={`omx-orb omx-orb-${state}`}
      aria-hidden="true"
    />
  )
}
