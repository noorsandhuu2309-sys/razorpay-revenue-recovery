// The gate. Nothing behind it renders until this screen is satisfied.
//
// It is one component with five modes rather than five routes, because the
// transitions between them are the whole experience: "wrong password" ->
// "forgot" -> "sent" -> "reset" is a single continuous repair, and routing it
// through the address bar would reload the app three times in the middle of
// someone's worst minute with the product.
//
// Two decisions worth stating:
//
//   * The screen chooses its own opening mode. A machine with no account at all
//     cannot sign in, so first launch opens on signup. Anything else opens on
//     signin. A gate that asks for credentials that do not exist yet is the
//     single most common way a local-first app looks broken on day one.
//
//   * Errors come from the server verbatim. `omnix/auth.py` deliberately
//     answers "Email or password is incorrect" for both halves so the endpoint
//     cannot be used to enumerate accounts, and a client that helpfully
//     reworded that into "no account with that email" would hand back exactly
//     what the backend spent effort withholding.

import { useEffect, useMemo, useRef, useState } from 'react'
import markUrl from '../assets/omnix-mark.png'
import {
  AuthFailure, forgot, resetPassword, signIn, signUp, useAuth,
} from '../lib/auth'
import { LoginCanvas } from './LoginCanvas'
import './login.css'

type Mode = 'signin' | 'signup' | 'forgot' | 'sent' | 'reset'

const MIN_PASSWORD = 12

/** The right panel's rotating line. Present in the original screen, and the
 *  one moving piece of text on it — a lock screen with a live status line
 *  reads as a system that is already running, which is the point. */
const PHRASES = [
  'Now running: claim verification',
  'Now running: independent-source corroboration',
  'Now running: multi-vendor challenge panel',
  'Now running: TERRA world graph',
]

/** Password strength as a count of satisfied rules, shown only on signup.
 *  Deliberately not a percentage or a word like "strong": the meter measures
 *  what it can check, and implying a verdict about a password's real entropy
 *  is a claim this cannot make. */
function rules(pw: string) {
  return [
    { label: `${MIN_PASSWORD}+ characters`, ok: pw.length >= MIN_PASSWORD },
    { label: 'a number', ok: /\d/.test(pw) },
    { label: 'a letter', ok: /[a-z]/i.test(pw) },
    { label: 'a symbol', ok: /[^\w\s]/.test(pw) },
  ]
}

export function Login() {
  const auth = useAuth()

  // A reset link lands as `/login?reset=<token>` (or `/?reset=…`, since the
  // SPA serves every path). Reading it here rather than in a router keeps the
  // token out of any history entry we create ourselves.
  const resetToken = useMemo(() => {
    try { return new URLSearchParams(window.location.search).get('reset') || '' }
    catch { return '' }
  }, [])

  const [mode, setMode] = useState<Mode>(
    resetToken ? 'reset' : auth.hasAccounts ? 'signin' : 'signup')
  /** The token the reset form will spend. Seeded from the URL, and replaced by
   *  the one `/api/auth/forgot` hands back on a local run — which is why this
   *  is state rather than the `resetToken` memo used directly. */
  const [token, setToken] = useState(resetToken)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const [keep, setKeep] = useState(true)
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [field, setField] = useState<string | null>(null)
  const [note, setNote] = useState('')
  /** Present only on a loopback run, where `/api/auth/forgot` returns the link
   *  it would otherwise have emailed. */
  const [devToken, setDevToken] = useState<string | null>(null)

  const firstRef = useRef<HTMLInputElement>(null)
  useEffect(() => { firstRef.current?.focus() }, [mode])

  const [phrase, setPhrase] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setPhrase((p) => (p + 1) % PHRASES.length), 3800)
    return () => clearInterval(t)
  }, [])

  // Clear the error when the user starts fixing it; a red message that outlives
  // the input it was about reads as a second, new failure.
  const edit = (fn: (v: string) => void) => (v: string) => {
    if (error) { setError(''); setField(null) }
    fn(v)
  }

  const go = (next: Mode) => {
    setMode(next); setError(''); setField(null); setNote('')
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true); setError(''); setField(null)
    try {
      if (mode === 'signin') await signIn(email, password, keep)
      else if (mode === 'signup') await signUp(name, email, password, keep, invite)
      else if (mode === 'reset') await resetPassword(token, password)
      else if (mode === 'forgot') {
        const r = await forgot(email)
        setDevToken(r.devToken)
        // No mail server exists on a loopback run, so the link has nowhere to
        // go but back to the person who asked for it. The server suppresses it
        // the moment the deployment looks hosted.
        setNote(r.devToken
          ? `${r.note} No mail server is configured locally, so continue below.`
          : r.note)
        setMode('sent')
      }
      // On success the auth store flips `authenticated` and the shell swaps
      // this component out. Nothing to navigate.
    } catch (err) {
      if (err instanceof AuthFailure) { setError(err.message); setField(err.field) }
      else setError('Could not reach OMNIX. Is the server running?')
    } finally {
      setBusy(false)
    }
  }

  const title = {
    signin: 'Sign in', signup: 'Create your account',
    forgot: 'Reset your password', sent: 'Check your inbox',
    reset: 'Choose a new password',
  }[mode]

  const blurb = {
    signin: 'Your Spaces, graph and conversations are waiting where you left them.',
    signup: auth.hasAccounts
      ? 'A second account gets its own sign-in. Spaces are shared on this machine.'
      : 'OMNIX stores everything locally. This account is the lock on it.',
    forgot: 'Enter the address on the account and OMNIX will issue a single-use link.',
    sent: 'If that account exists, a reset link has been issued. It expires in 30 minutes.',
    reset: 'This link works once. Every other session for the account will be signed out.',
  }[mode]

  const pwRules = rules(password)

  return (
    <div className="omx-gate">
      {/* Left: the card. Its two radial glows are pure CSS, not canvas — this
          half is the first thing a cold launch paints and it has to be instant
          even if WebGL never comes up. */}
      <div className="omx-gate-left">
        <div className="omx-gate-glow a" aria-hidden="true" />
        <div className="omx-gate-glow b" aria-hidden="true" />

        {/* The scroller is inside the glows, not around them. Both glows hang
            past the panel edge by design, and a scroll container counts that
            overhang as content — which added ~150px of dead scroll and a
            horizontal bar on a short window. */}
        <div className="omx-gate-scroll">
        <div className="omx-gate-panel">
        <div className="omx-gate-brand">
          <span
            className="mark"
            role="img"
            aria-label="OMNIX"
            style={{ maskImage: `url(${markUrl})`, WebkitMaskImage: `url(${markUrl})` }}
          />
          <div>
            <div className="bt">OMNIX</div>
            <div className="bs">Neural Executive</div>
          </div>
        </div>

        <h1 className="omx-gate-title">{title}</h1>
        <p className="omx-gate-blurb">{blurb}</p>

        {auth.demo && (
          <div className="omx-gate-demo" role="status">
            <span className="dot" aria-hidden="true" />
            Demo mode — any password opens this account.
          </div>
        )}

        {mode === 'sent' ? (
          <div className="omx-gate-sent">
            {note && <p className="omx-gate-note">{note}</p>}
            {devToken && (
              <button
                type="button"
                className="omx-gate-btn"
                onClick={() => {
                  // The token is already in hand, so going through the URL
                  // would only reload the bundle to reach the same form.
                  setToken(devToken)
                  setPassword('')
                  go('reset')
                }}
              >Continue to set a new password</button>
            )}
            <button type="button" className="omx-gate-link"
                    onClick={() => go('signin')}>Back to sign in</button>
          </div>
        ) : (
          <form className="omx-gate-form" onSubmit={submit} noValidate>
            {mode === 'signup' && (
              <label className={`omx-gate-fieldrow ${field === 'name' ? 'bad' : ''}`}>
                <span>Name</span>
                <input
                  ref={firstRef}
                  value={name}
                  autoComplete="name"
                  placeholder="How OMNIX should address you"
                  onChange={(e) => edit(setName)(e.target.value)}
                />
              </label>
            )}

            {(mode === 'signin' || mode === 'signup' || mode === 'forgot') && (
              <label className={`omx-gate-fieldrow ${field === 'email' ? 'bad' : ''}`}>
                <span>Email</span>
                <input
                  ref={mode === 'signup' ? undefined : firstRef}
                  type="email"
                  value={email}
                  autoComplete={mode === 'signup' ? 'email' : 'username'}
                  placeholder="you@example.com"
                  onChange={(e) => edit(setEmail)(e.target.value)}
                />
              </label>
            )}

            {mode !== 'forgot' && (
              <label className={`omx-gate-fieldrow ${field === 'password' ? 'bad' : ''}`}>
                <span>
                  {mode === 'reset' ? 'New password' : 'Password'}
                  {mode === 'signin' && (
                    <button type="button" className="omx-gate-inline"
                            onClick={() => go('forgot')}>Forgot?</button>
                  )}
                </span>
                <div className="omx-gate-pw">
                  <input
                    ref={mode === 'reset' ? firstRef : undefined}
                    type={show ? 'text' : 'password'}
                    value={password}
                    autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                    placeholder={mode === 'signin' ? 'Your password'
                      : `At least ${MIN_PASSWORD} characters`}
                    onChange={(e) => edit(setPassword)(e.target.value)}
                  />
                  <button type="button" className="omx-gate-eye"
                          onClick={() => setShow((s) => !s)}
                          aria-label={show ? 'Hide password' : 'Show password'}>
                    {show ? 'Hide' : 'Show'}
                  </button>
                </div>
              </label>
            )}

            {(mode === 'signup' || mode === 'reset') && password.length > 0 && (
              <ul className="omx-gate-rules">
                {pwRules.map((r) => (
                  <li key={r.label} className={r.ok ? 'ok' : ''}>{r.label}</li>
                ))}
              </ul>
            )}

            {mode === 'signup' && auth.inviteRequired && (
              <label className={`omx-gate-fieldrow ${field === 'invite' ? 'bad' : ''}`}>
                <span>Invite code</span>
                <input value={invite} placeholder="Your invite code"
                       onChange={(e) => edit(setInvite)(e.target.value)} />
              </label>
            )}

            {(mode === 'signin' || mode === 'signup') && (
              <label className="omx-gate-keep">
                <input type="checkbox" checked={keep}
                       onChange={(e) => setKeep(e.target.checked)} />
                <span>Keep me signed in on this machine</span>
              </label>
            )}

            {error && <div className="omx-gate-error" role="alert">{error}</div>}

            <button className="omx-gate-submit" type="submit" disabled={busy}>
              {busy ? <span className="omx-spin" /> : {
                signin: 'Sign in', signup: 'Create account',
                forgot: 'Send reset link', reset: 'Set password', sent: '',
              }[mode]}
            </button>
          </form>
        )}

        {mode !== 'sent' && (
          <div className="omx-gate-foot">
            {mode === 'signin' && (
              <>New to OMNIX?{' '}
                <button type="button" className="omx-gate-link"
                        onClick={() => go('signup')}>Create an account</button>
              </>
            )}
            {mode === 'signup' && auth.hasAccounts && (
              <>Already have an account?{' '}
                <button type="button" className="omx-gate-link"
                        onClick={() => go('signin')}>Sign in</button>
              </>
            )}
            {(mode === 'forgot' || mode === 'reset') && (
              <button type="button" className="omx-gate-link"
                      onClick={() => go('signin')}>Back to sign in</button>
            )}
          </div>
        )}

        {/* The honest footnote. OMNIX is loopback-only with no TLS, and a login
            screen that implies otherwise is the wrong kind of confidence. */}
        <div className="omx-gate-fine">
          Runs locally on this machine. Passwords are stretched with scrypt and
          never leave it.
        </div>
        </div>
        </div>
      </div>

      {/* Right: the field. Hidden under 900px, where there is no room for it
          and the card should have the whole screen. */}
      <div className="omx-gate-right" aria-hidden="true">
        <LoginCanvas />
        <div className="omx-gate-vignette" />
        <div className="omx-gate-fade" />

        <div className="omx-gate-hero">
          <div className="kicker">Neural Executive</div>
          <h2>One assistant.<br />Many minds.</h2>
          <p>
            OMNIX runs a council of specialised models — orchestrated,
            cross-examined, and resolved into an answer you can defend.
          </p>
          <div className="omx-gate-phrase">
            {/* Keyed so each line re-runs the fade rather than swapping in
                place, which reads as a glitch at this size. */}
            <span key={phrase}>{PHRASES[phrase]}</span>
          </div>
        </div>

        <div className="omx-gate-status">
          <span className="dot" />
          {auth.demo ? 'DEMO SESSION · ANY PASSWORD' : 'LOCAL SESSION · SAME-SITE LAX'}
        </div>
      </div>
    </div>
  )
}
