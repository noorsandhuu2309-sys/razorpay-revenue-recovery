// Voice: microphone capture for STT, and playback for TTS.
//
// Both endpoints have existed on the server the whole time — `/api/stt` runs
// faster-whisper, `/api/tts` runs piper — and were only ever dropped from the
// UI when the old bundle was retired.
//
// The one real wire constraint is the format. `/api/stt` opens the upload with
// Python's `wave` module and rejects anything that is not 16-bit PCM, so the
// browser's own MediaRecorder output (WebM/Opus) is unusable. We capture raw
// float samples through an AudioContext, downmix to mono, resample to 16 kHz
// and write the WAV header by hand.
//
// TWO THINGS HERE ARE NOT OBVIOUS AND BOTH MATTER:
//
// **Speech is chunked and pipelined.** Piper synthesises roughly in real time,
// so asking it for a 900-character answer means ~20 seconds of silence before a
// single word is heard. Instead the text is split at sentence boundaries and
// each chunk is fetched separately, with the NEXT chunk downloading while the
// current one plays. Time-to-first-word drops to about a second and stays there
// however long the answer is. Chunking client-side rather than adding a
// streaming endpoint also means Stop is instant: we simply never play chunk
// n+1, and the in-flight fetch is aborted.
//
// **Playback runs through an AnalyserNode.** Not for processing — so the UI can
// read the real amplitude envelope and drive the voice orb from the actual
// waveform. An animation on a timer looks synthetic the moment it keeps moving
// through a pause between words.

const TARGET_RATE = 16000

/** Encode mono float32 samples as a 16-bit PCM WAV, which is the only thing
 *  `/api/stt` will open. */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const str = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i))
  }
  str(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  str(8, 'WAVE')
  str(12, 'fmt ')
  view.setUint32(16, 16, true)      // PCM chunk size
  view.setUint16(20, 1, true)       // format: PCM
  view.setUint16(22, 1, true)       // channels: mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)  // byte rate
  view.setUint16(32, 2, true)       // block align
  view.setUint16(34, 16, true)      // bits per sample
  str(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  let o = 44
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

/** Nearest-neighbour resample. Whisper wants 16 kHz and hardware gives 44.1 or
 *  48; the quality difference against a windowed filter is inaudible to a
 *  speech model and this needs no dependency. */
function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input
  const ratio = from / to
  const out = new Float32Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i++) out[i] = input[Math.floor(i * ratio)]
  return out
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------
export interface Recorder {
  /** Resolves with the transcript, or '' if nothing was said. */
  stop: () => Promise<string>
  /** Abandon without transcribing. */
  cancel: () => void
  /** 0..1, for a live level meter. */
  level: () => number
}

export interface RecordOptions {
  /** End the turn automatically after this much trailing silence, once speech
   *  has actually been heard. Omit for push-to-talk. */
  silenceMs?: number
  /** Fired when the silence timer ends the turn, so hands-free mode knows to
   *  transcribe without the user pressing anything. */
  onAutoStop?: () => void
  /** Level ticks for the visualiser, ~60/s. */
  onLevel?: (level: number) => void
}

/** Level above which we consider the user to be talking. Set from measurement:
 *  a quiet room floors around 0.005–0.01 on laptop mics, ordinary speech peaks
 *  well past 0.05. */
const SPEECH_LEVEL = 0.035

/** Start capturing. Throws if the user denies the microphone, which the caller
 *  must surface — a dead mic button with no explanation is worse than no mic
 *  button. */
export async function startRecording(opts: RecordOptions = {}): Promise<Recorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  })
  const ctx = new AudioContext()
  const source = ctx.createMediaStreamSource(stream)
  // ScriptProcessor is deprecated in favour of AudioWorklet, but a worklet
  // needs a separate module file fetched at runtime and this app ships no
  // runtime-fetched assets. The node is torn down immediately after use.
  const node = ctx.createScriptProcessor(4096, 1, 1)
  const chunks: Float32Array[] = []
  let peak = 0
  let stopped = false
  let heardSpeech = false
  let quietSince = 0
  let autoStopped = false

  node.onaudioprocess = (e) => {
    if (stopped) return
    const data = e.inputBuffer.getChannelData(0)
    chunks.push(new Float32Array(data))
    let m = 0
    for (let i = 0; i < data.length; i += 16) m = Math.max(m, Math.abs(data[i]))
    peak = m
    opts.onLevel?.(m)

    if (!opts.silenceMs) return
    const now = performance.now()
    if (m > SPEECH_LEVEL) {
      heardSpeech = true
      quietSince = 0
      return
    }
    // Silence only counts AFTER something was said. Without this the turn ends
    // during the pause between pressing the button and starting to speak.
    if (!heardSpeech) return
    if (!quietSince) quietSince = now
    else if (now - quietSince > opts.silenceMs && !autoStopped) {
      autoStopped = true
      opts.onAutoStop?.()
    }
  }
  source.connect(node)
  // Routed to a muted gain rather than the speakers: ScriptProcessor does not
  // fire unless its output is connected, but connecting it to the destination
  // plays the microphone back at the user as feedback.
  const sink = ctx.createGain()
  sink.gain.value = 0
  node.connect(sink)
  sink.connect(ctx.destination)

  const teardown = () => {
    stopped = true
    try { node.disconnect(); source.disconnect(); sink.disconnect() } catch { /* already gone */ }
    for (const t of stream.getTracks()) t.stop()
    void ctx.close()
  }

  return {
    level: () => peak,
    cancel: teardown,
    stop: async () => {
      const rate = ctx.sampleRate
      teardown()
      const total = chunks.reduce((n, c) => n + c.length, 0)
      if (!total) return ''
      const flat = new Float32Array(total)
      let at = 0
      for (const c of chunks) { flat.set(c, at); at += c.length }

      // Silence in, nothing out. Sending an empty clip makes whisper
      // hallucinate a caption, which then gets sent as the user's question.
      let energy = 0
      for (let i = 0; i < flat.length; i += 8) energy += Math.abs(flat[i])
      if (energy / (flat.length / 8) < 0.004) return ''

      const wav = encodeWav(resample(flat, rate, TARGET_RATE), TARGET_RATE)
      const form = new FormData()
      form.append('audio', wav, 'speech.wav')
      const res = await fetch('/api/stt', {
        method: 'POST', body: form, credentials: 'include',
      })
      if (!res.ok) {
        let detail = `speech recognition failed (${res.status})`
        try { detail = (await res.json()).error || detail } catch { /* non-JSON */ }
        throw new Error(detail)
      }
      const body = await res.json() as { text?: string }
      return (body.text || '').trim()
    },
  }
}

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------

/** Split for synthesis: whole sentences, packed to a size Piper renders fast.
 *
 *  Measured on this machine, Piper synthesises at about **5x realtime**: a
 *  220-character chunk takes ~2.8s to render and then plays for ~14s. Two
 *  consequences shape the split:
 *
 *  * Once the first chunk is playing the pipeline never starves — each chunk
 *    buys far more playback time than the next one costs to fetch.
 *  * Time-to-first-word is therefore entirely decided by the size of the FIRST
 *    chunk. So the first one is deliberately small (a short sentence, ~90
 *    chars, ~0.6s) and the rest are full size. Speech starts in well under a
 *    second and the seam is inaudible, because the small chunk still ends on a
 *    sentence boundary.
 *
 *  Packing sentences rather than sending each alone matters for prosody: a
 *  string of one-clause requests reads flat and clipped.
 */
const FIRST_CHUNK = 90
const CHUNK = 220

function speechChunks(text: string): string[] {
  const sentences = text.split(/(?<=[.!?:;])\s+|\n+/).filter((s) => s.trim())
  const out: string[] = []
  let buf = ''
  const cap = () => (out.length === 0 ? FIRST_CHUNK : CHUNK)

  for (const s of sentences) {
    const piece = s.trim()
    if (!piece) continue
    if (buf && buf.length + piece.length + 1 > cap()) {
      out.push(buf)
      buf = piece
    } else {
      buf = buf ? `${buf} ${piece}` : piece
    }
    // A single sentence longer than the cap is broken on commas rather than
    // sent whole — one 600-character sentence would stall the pipeline.
    while (buf.length > cap() * 1.6) {
      const limit = cap()
      const cut = buf.lastIndexOf(',', limit)
      const at = cut > limit * 0.4 ? cut + 1 : limit
      out.push(buf.slice(0, at).trim())
      buf = buf.slice(at).trim()
    }
  }
  if (buf) out.push(buf)
  return out
}

export interface SpeechHandle {
  /** Stop immediately: cancels playback and any queued chunk. */
  stop: () => void
  /** Resolves when speech finishes, is stopped, or fails. */
  done: Promise<void>
}

let activeSpeech: { stop: () => void } | null = null
let level = 0

/** Current speech amplitude, 0..1. Read on a frame loop by the visualiser. */
export const speechLevel = () => level
export const isSpeaking = () => activeSpeech !== null

/** Stop whatever is being spoken. Safe to call when nothing is. */
export function stopSpeaking(): void {
  activeSpeech?.stop()
}

/** One AudioContext for all playback. Creating one per utterance leaks them —
 *  browsers cap the count and the cap is low. */
let playCtx: AudioContext | null = null
function audioContext(): AudioContext {
  if (!playCtx || playCtx.state === 'closed') playCtx = new AudioContext()
  // Autoplay policy suspends a context created before a user gesture.
  if (playCtx.state === 'suspended') void playCtx.resume()
  return playCtx
}

/**
 * Speak `text`, starting as soon as the first sentence is synthesised.
 *
 * @param onChunk fired with each chunk as it begins playing, so the UI can
 *                highlight the sentence being read.
 */
export function speak(
  text: string,
  onChunk?: (chunk: string, index: number, total: number) => void,
): SpeechHandle {
  stopSpeaking()

  const chunks = speechChunks((text || '').trim())
  let cancelled = false
  let currentSource: AudioBufferSourceNode | null = null
  const inflight = new AbortController()

  const stop = () => {
    cancelled = true
    inflight.abort()
    try { currentSource?.stop() } catch { /* already ended */ }
    currentSource = null
    level = 0
    if (activeSpeech === handle) activeSpeech = null
  }

  const handle = { stop }

  const done = (async () => {
    if (!chunks.length) return
    activeSpeech = handle

    const ctx = audioContext()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.75
    analyser.connect(ctx.destination)
    const bins = new Uint8Array(analyser.frequencyBinCount)

    let meter = 0
    const readLevel = () => {
      if (cancelled || activeSpeech !== handle) { level = 0; return }
      analyser.getByteTimeDomainData(bins)
      let peak = 0
      for (let i = 0; i < bins.length; i++) {
        peak = Math.max(peak, Math.abs(bins[i] - 128) / 128)
      }
      // Eased so the orb breathes rather than flickering on every syllable.
      level = level * 0.7 + Math.min(1, peak * 1.8) * 0.3
      meter = requestAnimationFrame(readLevel)
    }
    meter = requestAnimationFrame(readLevel)

    /** Fetch one chunk's audio. Returns null if it failed or we were stopped. */
    const fetchChunk = async (body: string): Promise<AudioBuffer | null> => {
      try {
        const res = await fetch('/api/tts', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: body }),
          signal: inflight.signal,
        })
        if (!res.ok) return null
        return await ctx.decodeAudioData(await res.arrayBuffer())
      } catch {
        return null   // aborted, offline, or the voice stack is not installed
      }
    }

    try {
      // The pipeline: chunk n+1 downloads while chunk n plays, so only the
      // very first request is ever waited on.
      let next: Promise<AudioBuffer | null> = fetchChunk(chunks[0])
      for (let i = 0; i < chunks.length; i++) {
        if (cancelled) break
        const buffer = await next
        next = i + 1 < chunks.length
          ? fetchChunk(chunks[i + 1])
          : Promise.resolve(null)
        if (cancelled) break
        if (!buffer) continue           // a bad chunk is skipped, not fatal

        onChunk?.(chunks[i], i, chunks.length)
        await new Promise<void>((resolve) => {
          const src = ctx.createBufferSource()
          src.buffer = buffer
          src.connect(analyser)
          src.onended = () => resolve()
          currentSource = src
          src.start()
        })
      }
    } finally {
      cancelAnimationFrame(meter)
      level = 0
      try { analyser.disconnect() } catch { /* already gone */ }
      if (activeSpeech === handle) activeSpeech = null
    }
  })()

  return { stop, done }
}

/** Strip what should not be read aloud. A model answer is written for the eye:
 *  markdown bullets, bold markers and code fences become noise in speech. */
export function speakable(text: string): string {
  return (text || '')
    .replace(/```[\s\S]*?```/g, ' — code block, shown on screen. ')
    .replace(/`([^`]+)`/g, '$1')
    // A markdown table read cell by cell is unlistenable.
    .replace(/^\s*\|.*\|\s*$/gm, '')
    .replace(/^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/gm, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*|__|[*_>]/g, '')
    // "1." at the start of a line is read as "one full stop"; make it natural.
    .replace(/^\s*(\d+)[.)]\s+/gm, '$1: ')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Whether the server actually has the voice stack installed.
 *
 *  Cached after the first answer: the answer cannot change without a server
 *  restart. Without this the mic and read-aloud buttons appear on a build that
 *  cannot use them, and clicking either produces a 500 the user has no way to
 *  interpret.
 *
 *  Asks `/api/voice/status`, which only checks that the packages import and the
 *  model file is on disk. It used to POST to `/api/tts` with the word "ok" and
 *  read the status code, so on every mount of Home a machine that *did* have
 *  voice installed ran a full Piper synthesis to answer a yes/no question. */
let voiceAvailable: boolean | null = null

export async function checkVoice(): Promise<boolean> {
  if (voiceAvailable !== null) return voiceAvailable
  try {
    const res = await fetch('/api/voice/status', { credentials: 'include' })
    if (!res.ok) {
      voiceAvailable = false
      return voiceAvailable
    }
    const body = (await res.json()) as { tts?: boolean }
    voiceAvailable = body.tts === true
  } catch {
    voiceAvailable = false
  }
  return voiceAvailable
}
