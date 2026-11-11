// Decodes /api/sim/snapshot.bin and computes everyone's position at a given minute.
// Layout is documented in api/app/routers/sim.py.

export const NONE = 65535

export interface Snapshot {
  n: number
  home: Float32Array
  s1: Float32Array
  s2: Float32Array
  times: Uint16Array // 6 per person
  district: Uint16Array
  colour: Uint8Array // RGBA per person
  mode: Uint8Array
  status: Uint8Array
  age: Uint8Array
  act1: Uint8Array
  act2: Uint8Array
}

export function decode(buf: ArrayBuffer): Snapshot {
  const magic = new TextDecoder().decode(new Uint8Array(buf, 0, 4))
  if (magic !== 'MPS1') throw new Error('The snapshot format is not recognised. Rebuild the API image.')
  const n = new DataView(buf).getUint32(4, true)
  let o = 8
  const f32 = (len: number) => { const a = new Float32Array(buf, o, len); o += len * 4; return a }
  const u16 = (len: number) => { const a = new Uint16Array(buf, o, len); o += len * 2; return a }
  const u8 = (len: number) => { const a = new Uint8Array(buf, o, len); o += len; return a }
  return {
    n,
    home: f32(2 * n), s1: f32(2 * n), s2: f32(2 * n),
    times: u16(6 * n), district: u16(n),
    colour: u8(4 * n), mode: u8(n), status: u8(n), age: u8(n), act1: u8(n), act2: u8(n),
  }
}

/** What someone is doing at minute t: 0 = home, 1 = at stop 1, 2 = at stop 2, 3 = travelling. */
export const AT_HOME = 0, AT_S1 = 1, AT_S2 = 2, MOVING = 3

/**
 * Fill `out` (2n floats) with positions at minute `t` and `state` (n bytes) with what each person is doing.
 * Travel is interpolated in a straight line; the selected person's real road route is drawn separately.
 */
export function positionsAt(s: Snapshot, t: number, out: Float32Array, state: Uint8Array): void {
  const { n, home, s1, s2, times } = s
  for (let i = 0; i < n; i++) {
    const k = i * 6, p = i * 2
    const t0 = times[k], t1 = times[k + 1]
    let ax = home[p], ay = home[p + 1], bx = ax, by = ay, f = 0, st = AT_HOME
    if (t1 !== NONE && t >= t0) {
      const t2 = times[k + 2], t3 = times[k + 3], t4 = times[k + 4], t5 = times[k + 5]
      const hasS2 = t3 !== NONE
      if (t < t1) { bx = s1[p]; by = s1[p + 1]; f = (t - t0) / Math.max(1, t1 - t0); st = MOVING }
      else if (t < t2) { ax = s1[p]; ay = s1[p + 1]; st = AT_S1 }
      else if (hasS2 && t < t3) { ax = s1[p]; ay = s1[p + 1]; bx = s2[p]; by = s2[p + 1]; f = (t - t2) / Math.max(1, t3 - t2); st = MOVING }
      else if (hasS2 && t < t4) { ax = s2[p]; ay = s2[p + 1]; st = AT_S2 }
      else if (t < t5) {
        const from = hasS2 ? s2 : s1, ts = hasS2 ? t4 : t2
        ax = from[p]; ay = from[p + 1]; f = (t - ts) / Math.max(1, t5 - ts); st = MOVING
      }
    }
    out[p] = ax + (bx - ax) * f
    out[p + 1] = ay + (by - ay) * f
    state[i] = st
  }
}

export const fmtTime = (m: number) => {
  const h = Math.floor(m / 60) % 24, mm = Math.floor(m % 60)
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}
