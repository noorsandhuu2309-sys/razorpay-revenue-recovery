// Voice for the NOVA bar: dictate, speak answers, and hands-free conversation.
//
// Three controls, because they are three different intentions and collapsing
// them into one toggle makes all of them ambiguous:
//
//   Mic       push to dictate. Fills the input; you still read it and press
//             send. This is the safe default — speech recognition is wrong
//             often enough that sending a transcript unseen is a bad trade.
//   Speaker   read answers aloud as they arrive. Independent of the mic: most
//             people want one or the other, not both.
//   Converse  hands-free. Listen, transcribe, send, speak the answer, listen
//             again — until the user stops it or nothing is said.
//
// Converse deliberately requires an explicit stop rather than ending on the
// first silence. A loop that quits whenever the room goes quiet for a second
// is one that quits mid-thought.

import { useCallback, useEffect, useRef, useState } from 'react'
import { speak, speakable, startRecording, stopSpeaking, type Recorder } from '../lib/voice'
import { IconConverse, IconMic, IconSpeaker, IconSpeakerOff, IconStop } from './Icons'

type Mode = 'idle' | 'dictating' | 'converse-listening' | 'converse-thinking' | 'converse-speaking'

export function VoiceControls({ onTranscript, onAsk, lastAnswer, asking }: {
  /** Dictation result — goes into the input for the user to check and send. */
  onTranscript: (text: string) => void
  /** Converse mode sends directly and resolves with the answer to read back. */
  onAsk: (text: string) => Promise<string>
  /** The most recent assistant text, for the speak-answers toggle. */
  lastAnswer: string
  asking: boolean
}) {
  const [mode, setMode] = useState<Mode>('idle')
  const [speakAnswers, setSpeakAnswers] = useState(() => {
    try { return localStorage.getItem('omx-speak-answers') === 'true' } catch { return false }
  })
  const [err, setErr] = useState('')
  const [level, setLevel] = useState(0)

  const recorderRef = useRef<Recorder | null>(null)
  const converseRef = useRef(false)
  // Set by each converse turn so the recorder's silence detector can end the
  // wait. A ref rather than state: it is written from an audio callback, and a
  // re-render per level tick would be 60 renders a second.
  const silenceResolve = useRef<(() => void) | null>(null)
  // Which answer has already been read, so a re-render does not replay it.
  const spokenRef = useRef<string>('')

  // Live level meter while the mic is open.
  useEffect(() => {
    if (mode !== 'dictating' && mode !== 'converse-listening') { setLevel(0); return }
    const id = setInterval(() => setLevel(recorderRef.current?.level() ?? 0), 100)
    return () => clearInterval(id)
  }, [mode])

  // Read answers aloud when the toggle is on and we are not in converse mode
  // (which does its own speaking, in sequence with its own listening).
  useEffect(() => {
    if (!speakAnswers || converseRef.current) return
    const text = speakable(lastAnswer)
    if (!text || text === spokenRef.current) return
    spokenRef.current = text
    // `speak` returns a handle, not a promise; `.done` is the promise.
    void speak(text).done.catch(() => { /* playback is best-effort */ })
  }, [lastAnswer, speakAnswers])

  const stopEverything = useCallback(() => {
    converseRef.current = false
    recorderRef.current?.cancel()
    recorderRef.current = null
    stopSpeaking()
    setMode('idle')
  }, [])

  // Tear down on unmount, or a mic stays hot after the view changes.
  useEffect(() => stopEverything, [stopEverything])

  const dictate = async () => {
    setErr('')
    if (mode === 'dictating') {
      const rec = recorderRef.current
      recorderRef.current = null
      setMode('idle')
      try {
        const text = await rec?.stop()
        if (text) onTranscript(text)
        else setErr('Nothing heard')
      } catch (e) {
        setErr(e instanceof Error ? e.message : 'Dictation failed')
      }
      return
    }
    try {
      recorderRef.current = await startRecording()
      setMode('dictating')
    } catch {
      setErr('Microphone unavailable — check the browser permission')
    }
  }

  const converse = async () => {
    if (converseRef.current) { stopEverything(); return }
    setErr('')
    converseRef.current = true
    try {
      // One turn at a time, in order: listen → transcribe → answer → speak.
      while (converseRef.current) {
        setMode('converse-listening')
        let rec: Recorder
        try {
          rec = await startRecording({
            silenceMs: 1400,
            onAutoStop: () => silenceResolve.current?.(),
          })
        } catch {
          setErr('Microphone unavailable — check the browser permission')
          break
        }
        recorderRef.current = rec

        // Ends on 1.4s of silence after speech, or a 30s cap so a hot mic in a
        // noisy room cannot run forever. The fixed six-second window this
        // replaced cut people off mid-sentence and then sat waiting through the
        // pauses of anyone who spoke briefly.
        await new Promise<void>((resolve) => {
          const cap = setTimeout(resolve, 30000)
          silenceResolve.current = () => { clearTimeout(cap); resolve() }
        })
        if (!converseRef.current) { rec.cancel(); break }

        recorderRef.current = null
        setMode('converse-thinking')
        let said = ''
        try { said = await rec.stop() } catch { said = '' }
        if (!said) {
          // Silence ends the loop. Anything else would leave a hot mic running
          // in a room where nobody is talking to it.
          setErr('Nothing heard — conversation ended')
          break
        }
        if (!converseRef.current) break

        const answer = await onAsk(said)
        if (!converseRef.current) break

        setMode('converse-speaking')
        spokenRef.current = speakable(answer)
        await speak(spokenRef.current).done
      }
    } finally {
      converseRef.current = false
      recorderRef.current?.cancel()
      recorderRef.current = null
      setMode('idle')
    }
  }

  const toggleSpeak = () => {
    const next = !speakAnswers
    setSpeakAnswers(next)
    if (!next) stopSpeaking()
    try { localStorage.setItem('omx-speak-answers', String(next)) } catch { /* private mode */ }
  }

  const listening = mode === 'dictating' || mode === 'converse-listening'
  const inConverse = mode.startsWith('converse')

  return (
    <div className="omx-voice">
      {err && <span className="omx-voice-err" title={err}>{err}</span>}

      {listening && (
        <span className="omx-voice-level" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <i key={i} style={{ transform: `scaleY(${Math.max(0.18, Math.min(1, level * 5 - i * 0.12))})` }} />
          ))}
        </span>
      )}
      {inConverse && (
        <span className="omx-label omx-voice-state">
          {mode === 'converse-listening' ? 'listening'
            : mode === 'converse-thinking' ? 'thinking' : 'speaking'}
        </span>
      )}

      <button
        className={`omx-btn icon ${mode === 'dictating' ? 'rec' : ''}`}
        onClick={() => void dictate()}
        disabled={inConverse || asking}
        title={mode === 'dictating' ? 'Stop and transcribe' : 'Dictate a question'}
        aria-label={mode === 'dictating' ? 'Stop and transcribe' : 'Dictate a question'}
      >
        {mode === 'dictating' ? <IconStop size={15} /> : <IconMic size={15} />}
      </button>

      <button
        className={`omx-btn icon ${speakAnswers ? 'on' : ''}`}
        onClick={toggleSpeak}
        title={speakAnswers ? 'Stop reading answers aloud' : 'Read answers aloud'}
        aria-label={speakAnswers ? 'Stop reading answers aloud' : 'Read answers aloud'}
        aria-pressed={speakAnswers}
      >
        {speakAnswers ? <IconSpeaker size={15} /> : <IconSpeakerOff size={15} />}
      </button>

      <button
        className={`omx-btn icon ${inConverse ? 'rec' : ''}`}
        onClick={() => void converse()}
        disabled={mode === 'dictating'}
        title={inConverse ? 'End the spoken conversation' : 'Hands-free — speak and be answered aloud'}
        aria-label={inConverse ? 'End the spoken conversation' : 'Start a spoken conversation'}
        aria-pressed={inConverse}
      >
        {inConverse ? <IconStop size={15} /> : <IconConverse size={15} />}
      </button>
    </div>
  )
}
