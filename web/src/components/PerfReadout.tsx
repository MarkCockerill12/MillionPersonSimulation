import { useEffect, useState } from 'react'
import type { LoadStats } from '../pages/MapPage'

const n = new Intl.NumberFormat('en-GB')

/** Live frame rate plus how the city was loaded: evidence that it stays smooth at this scale. */
export function PerfReadout({ load, routeMs, scale = 1 }: { load: LoadStats; routeMs: number | null; scale?: number }) {
  const [fps, setFps] = useState<number | null>(null)

  useEffect(() => {
    let raf = 0, frames = 0, since = performance.now()
    const tick = (now: number) => {
      frames++
      if (now - since >= 500) {
        setFps(Math.round((frames * 1000) / (now - since)))
        frames = 0
        since = now
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  const loading = load.ms == null
  return (
    <dl className="perf" aria-label="Performance">
      <div><dt>{scale > 1 ? `Agents on the GPU (benchmark ${scale}×)` : "Residents on the GPU"}</dt><dd>{n.format(load.people * scale)}</dd></div>
      <div><dt>Frame rate</dt><dd>{fps ?? '–'} fps</dd></div>
      <div>
        <dt>{loading ? `Streaming ${load.chunks}/${load.total || '…'} chunks` : 'City loaded in'}</dt>
        <dd>{loading ? `${(load.bytes / 1e6).toFixed(1)} MB` : `${(load.ms! / 1000).toFixed(2)} s, ${(load.bytes / 1e6).toFixed(1)} MB`}</dd>
      </div>
      {routeMs != null && <div><dt>Last record and route</dt><dd>{Math.round(routeMs)} ms</dd></div>}
    </dl>
  )
}
