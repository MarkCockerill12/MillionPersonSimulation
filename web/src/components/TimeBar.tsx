import { fmtTime } from '../sim'

interface Props {
  minute: number
  setMinute: (m: number) => void
  playing: boolean
  setPlaying: (p: boolean) => void
  speed: number
  setSpeed: (s: number) => void
}

const SPEEDS = [4, 12, 30, 60]

export function TimeBar({ minute, setMinute, playing, setPlaying, speed, setSpeed }: Props) {
  return (
    <div className="timebar" role="group" aria-label="Time of day">
      <button className="play" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
        {playing ? (
          <svg viewBox="0 0 16 16" aria-hidden><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
        ) : (
          <svg viewBox="0 0 16 16" aria-hidden><path d="M4 2.5v11l9.5-5.5z" /></svg>
        )}
      </button>
      <output className="clock" aria-live="off">{fmtTime(minute)}</output>
      <div className="scrub">
        <input
          type="range" min={0} max={1439} step={1} value={Math.floor(minute)}
          onChange={(e) => setMinute(Number(e.target.value))}
          aria-label="Time of day"
        />
        <div className="ticks" aria-hidden>
          {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => <span key={h} style={{ left: `${(h / 24) * 100}%` }}>{String(h).padStart(2, '0')}</span>)}
        </div>
      </div>
      <label className="speed">
        Speed
        <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
          {SPEEDS.map((s) => <option key={s} value={s}>{s} min/s</option>)}
        </select>
      </label>
    </div>
  )
}
