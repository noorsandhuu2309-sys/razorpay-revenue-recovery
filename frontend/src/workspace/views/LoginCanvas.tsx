// The field behind the lock screen: a dithered Perlin flow in OMNIX gold.
//
// Lifted from the original pre-workspace login (`omnix/web/login.html` on the
// `worktree-omnix-login-auth` branch), which drew it in WebGL and is the look
// this screen is meant to have. The shader is unchanged; what is new is that
// it now cleans up after itself, because the React screen unmounts the moment
// sign-in succeeds and the old one never had to.
//
// It degrades rather than fails: no WebGL context, a shader that will not
// compile, or `prefers-reduced-motion` each leave the panel as a flat field
// with the CSS gradients still over it. None of them are worth a broken login.

import { useEffect, useRef } from 'react'

const VERT = 'attribute vec2 p; void main(){ gl_Position = vec4(p,0.0,1.0); }'

const FRAG = [
  'precision highp float;',
  'uniform vec2 uRes; uniform float uTime; uniform vec3 waveColor;',
  'uniform float uPixel; uniform vec2 uMouse; uniform float uMouseOn;',
  'uniform float waveSpeed; uniform float waveFreq; uniform float waveAmp;',
  'uniform float colorNum; uniform float mouseRadius;',
  'vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x,289.0);}',
  'vec2 fade(vec2 t){return t*t*t*(t*(t*6.0-15.0)+10.0);}',
  'float cnoise(vec2 P){',
  ' vec4 Pi=floor(P.xyxy)+vec4(0.0,0.0,1.0,1.0);',
  ' vec4 Pf=fract(P.xyxy)-vec4(0.0,0.0,1.0,1.0);',
  ' Pi=mod(Pi,289.0);',
  ' vec4 ix=Pi.xzxz; vec4 iy=Pi.yyww; vec4 fx=Pf.xzxz; vec4 fy=Pf.yyww;',
  ' vec4 i=permute(permute(ix)+iy);',
  ' vec4 gx=2.0*fract(i*0.0243902439024390)-1.0; vec4 gy=abs(gx)-0.5;',
  ' vec4 tx=floor(gx+0.5); gx=gx-tx;',
  ' vec2 g00=vec2(gx.x,gy.x); vec2 g10=vec2(gx.y,gy.y); vec2 g01=vec2(gx.z,gy.z); vec2 g11=vec2(gx.w,gy.w);',
  ' vec4 nz=1.79284291400159-0.85373472095314*vec4(dot(g00,g00),dot(g01,g01),dot(g10,g10),dot(g11,g11));',
  ' g00*=nz.x; g01*=nz.y; g10*=nz.z; g11*=nz.w;',
  ' float n00=dot(g00,vec2(fx.x,fy.x)); float n10=dot(g10,vec2(fx.y,fy.y));',
  ' float n01=dot(g01,vec2(fx.z,fy.z)); float n11=dot(g11,vec2(fx.w,fy.w));',
  ' vec2 fxy=fade(Pf.xy);',
  ' vec2 n_x=mix(vec2(n00,n01),vec2(n10,n11),fxy.x);',
  ' return 2.3*mix(n_x.x,n_x.y,fxy.y);',
  '}',
  'float fbm(vec2 p){ float v=0.0; float a=1.0; for(int i=0;i<8;i++){ v+=a*abs(cnoise(p)); p*=waveFreq; a*=waveAmp; } return v; }',
  'float pattern(vec2 p){ vec2 p2=p-uTime*waveSpeed; return fbm(p-fbm(p+fbm(p2))); }',
  'float bayer2(vec2 a){ a=floor(a); return fract(a.x/2.0+a.y*a.y*0.75); }',
  'float bayer4(vec2 a){ return bayer2(0.5*a)*0.25+bayer2(a); }',
  'float bayer8(vec2 a){ return bayer4(0.5*a)*0.25+bayer2(a); }',
  'void main(){',
  ' vec2 pixel=floor(gl_FragCoord.xy/uPixel)*uPixel;',
  ' vec2 uv=pixel/uRes;',
  ' vec2 cuv=(uv-0.5)*1.6; cuv.x*=uRes.x/uRes.y;',
  ' float f=pattern(cuv);',
  ' if(uMouseOn>0.5){ vec2 m=(uMouse-0.5)*1.6; m.x*=uRes.x/uRes.y; float d=length(cuv-m); f-=0.5*(1.0-smoothstep(0.0,mouseRadius,d)); }',
  ' vec3 col=mix(vec3(0.0),waveColor,clamp(f,0.0,1.0));',
  ' float thr=bayer8(pixel/uPixel)-0.5;',
  ' col+=thr/colorNum;',
  ' col=floor(col*(colorNum-1.0)+0.5)/(colorNum-1.0);',
  ' gl_FragColor=vec4(col,1.0);',
  '}',
].join('\n')

const PIXEL = 2.0

/** `--omx-accent-rgb` as shader-ready 0..1 floats, falling back to the gold the
 *  original screen was built in. The token is a bare "r, g, b" triple because
 *  the rest of the sheet composes it into `rgba(...)`. */
function accent(): [number, number, number] {
  const gold: [number, number, number] = [0.804, 0.643, 0.361]
  try {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue('--omx-accent-rgb').trim()
    const p = raw.split(',').map((n) => parseFloat(n) / 255)
    if (p.length === 3 && p.every((n) => Number.isFinite(n))) {
      return [p[0], p[1], p[2]]
    }
  } catch { /* fall through */ }
  return gold
}

export function LoginCanvas() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const gl = canvas.getContext('webgl', {
      antialias: true, alpha: false, premultipliedAlpha: false,
    })
    if (!gl) return

    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src)
      gl.compileShader(s)
      // A driver that rejects the shader would otherwise leave a black
      // rectangle that looks exactly like a failed page load.
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { gl.deleteShader(s); return null }
      return s
    }
    const vs = compile(gl.VERTEX_SHADER, VERT)
    const fs = compile(gl.FRAGMENT_SHADER, FRAG)
    if (!vs || !fs) return

    const prog = gl.createProgram()!
    gl.attachShader(prog, vs)
    gl.attachShader(prog, fs)
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]),
                  gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, 'p')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)

    const U = (n: string) => gl.getUniformLocation(prog, n)
    const uRes = U('uRes'), uTime = U('uTime'), uPixel = U('uPixel'), uMouse = U('uMouse')
    // The original hard-coded OMNIX gold. Reading the live accent token instead
    // keeps the field in step with the theme the user picked, and the default
    // accent is that same gold — so the screen still opens the way it did.
    gl.uniform3f(U('waveColor'), ...accent())
    gl.uniform1f(U('waveSpeed'), 0.05)
    gl.uniform1f(U('waveFreq'), 3.0)
    gl.uniform1f(U('waveAmp'), 0.3)
    gl.uniform1f(U('colorNum'), 4.0)
    gl.uniform1f(U('mouseRadius'), 1.0)
    gl.uniform1f(U('uMouseOn'), 1.0)

    let mx = 0.5, my = 0.5
    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      mx = (e.clientX - r.left) / r.width
      my = 1.0 - (e.clientY - r.top) / r.height
    }
    const onLeave = () => { mx = 0.5; my = 0.5 }
    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerleave', onLeave)

    // The drawing buffer has to cover the panel EXACTLY, and two things used to
    // stop it doing that, both of which showed up as an unpainted black strip
    // down the right-hand edge:
    //
    //   * The panel's width is fractional — `flex: 1` beside a `clamp()` column
    //     measures 1999.2px on a 2560px window — and the old code ROUNDED it.
    //     1999 buffer pixels stretched across 1999.2 CSS pixels leaves the last
    //     column of the panel with nothing drawn in it. Hence `ceil`: it is
    //     always better to render a fraction of a pixel too much than to leave
    //     a gap, since the excess is simply scaled back down.
    //   * The only trigger was `window.resize`. That misses everything else
    //     that changes this box: the fonts finishing (which re-lays the card
    //     column beside it), a devicePixelRatio change from dragging the window
    //     to a second monitor, and the first layout itself, which on some mount
    //     paths has not happened when the effect runs — measuring 0 there left
    //     a 1x1 buffer stretched over the whole panel until something resized.
    //
    // A ResizeObserver watches the box itself and so catches all of them,
    // including the window resize the old handler existed for.
    let lastW = -1, lastH = -1
    const resize = () => {
      // Capped at 1.5: this is a full-viewport fragment shader and a 3x retina
      // panel costs four times the fill for no visible gain through the dither.
      const scale = Math.min(window.devicePixelRatio || 1, 1.5)
      const r = canvas.getBoundingClientRect()
      const w = Math.max(1, Math.ceil(r.width * scale))
      const h = Math.max(1, Math.ceil(r.height * scale))
      // Assigning canvas.width reallocates and clears the buffer, so it must
      // not run on every observer callback — a resize that changed nothing
      // would drop a frame to black.
      if (w === lastW && h === lastH) return
      lastW = w; lastH = h
      canvas.width = w
      canvas.height = h
      gl.viewport(0, 0, w, h)
      gl.uniform2f(uRes, w, h)
      gl.uniform1f(uPixel, PIXEL * scale)
    }
    resize()

    const ro = new ResizeObserver(resize)
    ro.observe(canvas)
    // The observer does not fire for a DPR change on its own — the CSS box is
    // the same size, only the backing density moved.
    window.addEventListener('resize', resize)

    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const start = performance.now()
    let raf = 0
    const draw = () => {
      gl.uniform1f(uTime, (performance.now() - start) / 1000)
      gl.uniform2f(uMouse, mx, my)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      if (!reduce) raf = requestAnimationFrame(draw)
    }
    draw()

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', resize)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerleave', onLeave)
      // Contexts are a hard-capped browser resource (~16). Signing out and in
      // a few times would exhaust them and silently stop drawing.
      gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
  }, [])

  return <canvas ref={ref} className="omx-gate-canvas" aria-hidden="true" />
}
