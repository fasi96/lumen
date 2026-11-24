// Test-only: the share worker with a stand-in for Workers AI, which `wrangler dev` can't run
// locally. Whisper "hears" the word "cloud" once a second and gives the length from the WAV
// header; the small LLM answers {}. Used by test/smoke.mjs and Lumen Notes' end-to-end test
// (startDevWorker({ ai: true })). Never deployed: dev/wrangler.ai.toml points here.
import worker from "../src/index.js"

function wavSeconds(b64) {
  const head = Uint8Array.from(atob(b64.slice(0, 64)), (c) => c.charCodeAt(0))
  const rate = new DataView(head.buffer).getUint32(28, true)
  return rate ? (Math.floor(b64.length * 3 / 4) - 44) / rate : 0
}

const AI = {
  async run(model, input) {
    if (/whisper/.test(model)) {
      const duration = wavSeconds(input.audio)
      const words = Array.from({ length: Math.max(1, Math.round(duration)) }, (_, i) => ({ word: "cloud", start: i, end: i + 0.8 }))
      return { transcription_info: { language: input.language || "en", duration }, segments: [{ words }] }
    }
    return { response: "{}" }
  },
}

export default { fetch: (req, env, ctx) => worker.fetch(req, { ...env, AI }, ctx) }
