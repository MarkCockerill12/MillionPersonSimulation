// Snapshot format v2: the city is split into chunks that download in parallel and render as they land.
// Each chunk is columnar and quantised (32 bytes per person). See generator/src/snapshot.py.
//
//   0   char[4] "MPS2"   4 u32 start   8 u32 count   12 u32 reserved
//   16  u16[2c] home   u16[2c] stop1   u16[2c] stop2      (unorm16 within manifest.bbox)
//       u16[4c] timesA (leave home, arrive s1, leave s1, arrive s2)
//       u16[2c] timesB (leave s2, arrive home)              (minutes, 65535 = none)
//       u8[4c]  colour RGBA   u8[4c] meta (mode, age, act1, act2)

export const NONE = 65535

export interface Manifest {
  version: number
  n: number
  bbox: [number, number, number, number]
  chunks: { url: string; start: number; count: number; bytes: number }[]
}

export interface Chunk {
  start: number
  count: number
  home: Uint16Array
  s1: Uint16Array
  s2: Uint16Array
  timesA: Uint16Array
  timesB: Uint16Array
  colour: Uint8Array
  meta: Uint8Array
  attributes: Record<string, { value: ArrayBufferView; size: number }>
  /** Stable object handed to deck.gl as layer data. Keeping its identity fixed means deck never re-uploads the
   *  buffers. Only the time uniform changes per frame. */
  data: { length: number; attributes: Record<string, { value: ArrayBufferView; size: number }> }
}

export function decodeChunk(buf: ArrayBuffer): Chunk {
  const magic = new TextDecoder().decode(new Uint8Array(buf, 0, 4))
  if (magic !== 'MPS2') throw new Error('The map snapshot is out of date. Re-run the generator.')
  const dv = new DataView(buf)
  const start = dv.getUint32(4, true), c = dv.getUint32(8, true)
  let o = 16
  const u16 = (len: number) => { const a = new Uint16Array(buf, o, len); o += len * 2; return a }
  const u8 = (len: number) => { const a = new Uint8Array(buf, o, len); o += len; return a }
  const home = u16(2 * c), s1 = u16(2 * c), s2 = u16(2 * c), timesA = u16(4 * c), timesB = u16(2 * c)
  const colour = u8(4 * c), meta = u8(4 * c)
  const attributes = {
      instHome: { value: home, size: 2 }, instS1: { value: s1, size: 2 }, instS2: { value: s2, size: 2 },
      instTimesA: { value: timesA, size: 4 }, instTimesB: { value: timesB, size: 2 },
      instColours: { value: colour, size: 4 }, instMeta: { value: meta, size: 4 },
  }
  return { start, count: c, home, s1, s2, timesA, timesB, colour, meta, attributes, data: { length: c, attributes } }
}

/** CPU mirror of the shader, used for a single person (the selected one). */
export function positionOf(ch: Chunk, i: number, t: number, bbox: Manifest['bbox']): [number, number] {
  const dq = (a: Uint16Array, k: number): [number, number] =>
    [bbox[0] + (a[k] / 65535) * (bbox[2] - bbox[0]), bbox[1] + (a[k + 1] / 65535) * (bbox[3] - bbox[1])]
  const home = dq(ch.home, i * 2), s1 = dq(ch.s1, i * 2), s2 = dq(ch.s2, i * 2)
  const [t0, t1, t2, t3] = ch.timesA.subarray(i * 4, i * 4 + 4), [t4, t5] = ch.timesB.subarray(i * 2, i * 2 + 2)
  const lerp = (a: [number, number], b: [number, number], f: number): [number, number] => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
  if (t1 === NONE || t < t0) return home
  const hasS2 = t3 !== NONE
  if (t < t1) return lerp(home, s1, (t - t0) / Math.max(1, t1 - t0))
  if (t < t2) return s1
  if (hasS2 && t < t3) return lerp(s1, s2, (t - t2) / Math.max(1, t3 - t2))
  if (hasS2 && t < t4) return s2
  if (t < t5) {
    const from = hasS2 ? s2 : s1, ts = hasS2 ? t4 : t2
    return lerp(from, home, (t - ts) / Math.max(1, t5 - ts))
  }
  return home
}

/** Which leg of the day person i is travelling at minute t (leg = seq of the stop they left), or null if stationary. */
export function legAt(ch: Chunk, i: number, t: number): { leg: number; f: number } | null {
  const [t0, t1, t2, t3] = ch.timesA.subarray(i * 4, i * 4 + 4), [t4, t5] = ch.timesB.subarray(i * 2, i * 2 + 2)
  if (t1 === NONE || t < t0) return null
  const hasS2 = t3 !== NONE
  if (t < t1) return { leg: 0, f: (t - t0) / Math.max(1, t1 - t0) }
  if (t < t2) return null
  if (hasS2 && t < t3) return { leg: 1, f: (t - t2) / Math.max(1, t3 - t2) }
  if (hasS2 && t < t4) return null
  if (t < t5) return hasS2 ? { leg: 2, f: (t - t4) / Math.max(1, t5 - t4) } : { leg: 1, f: (t - t2) / Math.max(1, t5 - t2) }
  return null
}

/** Point a fraction f of the way along a polyline (planar approximation, fine at city scale). */
export function alongLine(coords: number[][], f: number): [number, number] {
  const seg: number[] = []
  let total = 0
  for (let k = 1; k < coords.length; k++) {
    const d = Math.hypot(coords[k][0] - coords[k - 1][0], coords[k][1] - coords[k - 1][1])
    seg.push(d); total += d
  }
  let target = Math.min(1, Math.max(0, f)) * total
  for (let k = 0; k < seg.length; k++) {
    if (target <= seg[k] || k === seg.length - 1) {
      const r = seg[k] ? Math.min(1, target / seg[k]) : 0
      return [coords[k][0] + (coords[k + 1][0] - coords[k][0]) * r, coords[k][1] + (coords[k + 1][1] - coords[k][1]) * r]
    }
    target -= seg[k]
  }
  return coords[0] as [number, number]
}

export const fmtTime = (m: number) => {
  const h = Math.floor(m / 60) % 24, mm = Math.floor(m % 60)
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}
