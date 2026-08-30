// Hands-free voice mode: talk to OMNIX and be answered out loud.
//
// A full overlay rather than a control in the composer, because it is a
// different mode of use rather than a different button. While it is open the
// screen is not the interface — the point is that you are not looking at it —
// so it shows only what you need to know the machine is with you: which state
// the turn is in, the orb moving to your actual voice, and the last exchange in
// large text for when you glance back.
//
// The loop is: listen → transcribe → answer → speak → listen again. Each stage
// can be interrupted, and every exit path stops the microphone. A voice feature
// that leaves a hot mic behind is not a bug you get to fix later.
//
// Barge-in is deliberately NOT implemented. Cutting the assistant off the
// moment the room makes a noise sounds clever and behaves badly: the speaker
// output re-enters the microphone, so it interrupts itself. Stopping is a
// button, and the button is large.

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  isSpeaking, speak, speakable, speechLevel, startRecording, stopSpeaking,
  type Recorder,
} from '../lib/voice'
import { VoiceOrb, type OrbState } from './VoiceOrb'
import { IconClose, IconMic, IconStop } from './Icons'

export function VoiceMode({ onAsk, onClose }: {
  /** Send the transcript and resolve with the answer text to read back. */
  onAsk: (text: string) => Promise<string>
  onClose: () => void
}) {
  const [state, setState] = useState<OrbState>('idle')
  const [heard, setHeard] = useState('')
  const [reply, setReply] = useState('')
  const [error, setError] = useState('')

  const running = useRef(false)
  const recorder = useRef<Recorder | null>(null)
  const micLevel = useRef(0)
  const endTurn = useRef<(() => void) | null>(null)

  // One getter for the orb, switching source with the state. The orb reads it
  // every frame; it must never allocate or re-render.
  const level = useCallback(
    () => (isSpeaking() ? speechLevel() : micLevel.current), [])

  const shutdown = useCallback(() => {
    running.current = false
    endTurn.current?.()
    recorder.current?.cancel()
    recorder.current = null
    stopSpeaking()
    setState('idle')
  }, [])

  // Unmount must kill the mic. This is the path that actually fires when the
  // user navigates away mid-conversation.
  useEffect(() => shutdown, [shutdown])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { shutdown(); onClose() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [shutdown, onClose])

  const loop = useCallback(async () => {
    if (running.current) return
    running.current = true
    setError('')

    try {
      while (running.current) {
        // -- listen ------------------------------------------------------
        setState('listening')
        setHeard('')
        let rec: Recorder
        try {
          rec = await startRecording({
            silenceMs: 1400,
            onAutoStop: () => endTurn.current?.(),
            onLevel: (l) => { micLevel.current = l },
          })
        } catch {
          setError('OMNIX cannot reach your microphone. Check the browser '
                   + 'permission for this site.')
          break
        }
        recorder.current = rec

        // Resolves on trailing silence, on the Stop button, or at a 30s cap so
        // an open mic in a noisy room cannot run indefinitely.
        await new Promise<void>((resolve) => {
          const cap = setTimeout(resolve, 30000)
          endTurn.current = () => { clearTimeout(cap); resolve() }
        })
        endTurn.current = null
        if (!running.current) { rec.cancel(); break }

        // -- transcribe --------------------------------------------------
        setState('thinking')
        micLevel.current = 0
        recorder.current = null
        let said = ''
        try {
          said = await rec.stop()
        } catch (e) {
          setError(e instanceof Error ? e.message : 'Could not transcribe that.')
          break
        }
        if (!said) {
          setError('Nothing heard. Tap the orb to start again.')
          break
        }
        if (!running.current) break
        setHeard(said)

        // -- answer ------------------------------------------------------
        let answer = ''
        try {
          answer = await onAsk(said)
        } catch (e) {
          setError(e instanceof Error ? e.message : 'That question failed.')
          break
        }
        if (!running.current) break
        setReply(answer)

        // -- speak -------------------------------------------------------
        const spoken = speakable(answer)
        if (spoken) {
          setState('speaking')
          await speak(spoken).done
        }
        if (!running.current) break
      }
    } finally {
      running.current = false
      recorder.current?.cancel()
      recorder.current = null
      micLevel.current = 0
      setState('idle')
    }
  }, [onAsk])

  const label = state === 'listening' ? 'Listening'
    : state === 'thinking' ? 'Thinking'
    : state === 'speaking' ? 'Speaking'
    : 'Tap to talk'

  const hint = state === 'listening' ? 'Stop talking and it will answer'
    : state === 'thinking' ? 'Working on it'
    : state === 'speaking' ? 'Tap stop to interrupt'
    : 'OMNIX will listen, answer, and listen again'

  return (
    <div className="omx-vm" role="dialog" aria-modal="true" aria-label="Voice mode">
      <button className="omx-vm-close" onClick={() => { shutdown(); onClose() }}
              title="Close voice mode — Esc" aria-label="Close voice mode">
        <IconClose size={16} />
      </button>

      <div className="omx-vm-stage">
        <button
          className="omx-vm-orb"
          onClick={() => {
            if (running.current) {
              // Mid-turn: Stop ends the whole conversation rather than just
              // this stage. Anything subtler needs explaining, and there is no
              // room to explain it to someone who is not looking at the screen.
              shutdown()
            } else {
              void loop()
            }
          }}
          aria-label={running.current ? 'Stop the conversation' : 'Start talking'}
        >
          <VoiceOrb state={state} level={level} size={168} />
          <span className="omx-vm-icon" aria-hidden="true">
            {state === 'idle' ? <IconMic size={20} /> : <IconStop size={18} />}
          </span>
        </button>

        <div className={`omx-vm-state s-${state}`}>{label}</div>
        <div className="omx-vm-hint">{hint}</div>

        {error && <div className="omx-vm-error">{error}</div>}

        {heard && (
          <div className="omx-vm-said">
            <span className="omx-label">You said</span>
            <p>{heard}</p>
          </div>
        )}
        {reply && state !== 'listening' && (
          <div className="omx-vm-reply">
            <span className="omx-label">OMNIX</span>
            {/* Plain text, not markdown: this is a glance-at surface while
                somebody is listening, and the full formatted answer is already
                in the transcript behind this overlay. */}
            <p>{speakable(reply).slice(0, 420)}
              {speakable(reply).length > 420 ? '…' : ''}</p>
          </div>
        )}
      </div>

      <div className="omx-vm-foot">
        Everything said here is also written into the conversation behind this
        screen.
      </div>
    </div>
  )
}
