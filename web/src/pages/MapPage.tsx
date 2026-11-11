import { useCallback, useEffect, useMemo, useState } from 'react'
import DeckGL from '@deck.gl/react'
import { ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers'
import { WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { Map } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
import { setWorkerUrl } from 'maplibre-gl'
// MapLibre 6 ships its tile worker as a separate ES module; let Vite bundle it and tell MapLibre where it is.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { api, type Person } from '../api'
import { decode, positionsAt, AT_HOME, AT_S1, AT_S2, MOVING, type Snapshot } from '../sim'
import { PersonPanel } from '../components/PersonPanel'
import { TimeBar } from '../components/TimeBar'
import { Search } from '../components/Search'

setWorkerUrl(maplibreWorkerUrl)

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const INITIAL: MapViewState = { longitude: -1.8904, latitude: 52.4862, zoom: 10.6, pitch: 0, bearing: 0 }

export type ColourBy = 'signature' | 'activity' | 'mode' | 'age'

// Activity codes (sim ACTIVITIES): home, work, school, university, shopping, food_drink, leisure, healthcare
const ACTIVITY_RGB: [number, number, number][] = [
  [74, 110, 140], [222, 178, 60], [95, 184, 176], [95, 184, 176], [224, 122, 155], [224, 122, 155], [150, 130, 230], [235, 96, 80],
]
const MOVING_RGB: [number, number, number] = [245, 245, 240]
// MODES: none, walk, cycle, bus, train, car
const MODE_RGB: [number, number, number][] = [
  [70, 84, 96], [155, 211, 122], [95, 184, 176], [222, 178, 60], [150, 130, 230], [235, 110, 90],
]

const LEGENDS: Record<ColourBy, { label: string; rgb: [number, number, number] }[]> = {
  signature: [],
  activity: [
    { label: 'Home', rgb: ACTIVITY_RGB[0] }, { label: 'Work', rgb: ACTIVITY_RGB[1] },
    { label: 'School or university', rgb: ACTIVITY_RGB[2] }, { label: 'Shopping or eating out', rgb: ACTIVITY_RGB[4] },
    { label: 'Leisure', rgb: ACTIVITY_RGB[6] }, { label: 'Healthcare', rgb: ACTIVITY_RGB[7] }, { label: 'Travelling', rgb: MOVING_RGB },
  ],
  mode: [
    { label: 'Car', rgb: MODE_RGB[5] }, { label: 'Bus', rgb: MODE_RGB[3] }, { label: 'Train', rgb: MODE_RGB[4] },
    { label: 'Cycle', rgb: MODE_RGB[2] }, { label: 'Walk', rgb: MODE_RGB[1] }, { label: 'Stays home', rgb: MODE_RGB[0] },
  ],
  age: [
    { label: '0–17', rgb: [95, 184, 176] }, { label: '18–39', rgb: [155, 211, 122] },
    { label: '40–64', rgb: [222, 178, 60] }, { label: '65+', rgb: [235, 110, 90] },
  ],
}

function ageRgb(a: number): [number, number, number] {
  return a < 18 ? [95, 184, 176] : a < 40 ? [155, 211, 122] : a < 65 ? [222, 178, 60] : [235, 110, 90]
}

function paint(s: Snapshot, by: ColourBy, state: Uint8Array, out: Uint8Array) {
  if (by === 'signature') { out.set(s.colour); return }
  for (let i = 0; i < s.n; i++) {
    let c: [number, number, number]
    if (by === 'mode') c = MODE_RGB[s.mode[i]] ?? MODE_RGB[0]
    else if (by === 'age') c = ageRgb(s.age[i])
    else {
      const st = state[i]
      c = st === MOVING ? MOVING_RGB : st === AT_HOME ? ACTIVITY_RGB[0] : ACTIVITY_RGB[st === AT_S1 ? s.act1[i] : st === AT_S2 ? s.act2[i] : 0]
    }
    const o = i * 4
    out[o] = c[0]; out[o + 1] = c[1]; out[o + 2] = c[2]; out[o + 3] = 255
  }
}

export default function MapPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [progress, setProgress] = useState<string>('Connecting to the database…')
  const [error, setError] = useState<string | null>(null)
  const [minute, setMinute] = useState(() => {
    const t = new URLSearchParams(window.location.search).get('t')?.match(/^(\d{1,2}):(\d{2})$/)
    return t ? Number(t[1]) * 60 + Number(t[2]) : 8 * 60 // ?t=HH:MM sets the starting clock
  })
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(12) // simulated minutes per real second
  const [colourBy, setColourBy] = useState<ColourBy>('activity')
  const [selected, setSelected] = useState<number | null>(null)
  const [person, setPerson] = useState<Person | null>(null)
  const [route, setRoute] = useState<GeoJSON.FeatureCollection | null>(null)
  const [viewState, setViewState] = useState<MapViewState>(INITIAL)

  // Typed arrays are allocated once and mutated in place each frame; `frame` tells deck.gl to re-upload.
  const [bufs, setBufs] = useState<{ pos: Float32Array; col: Uint8Array; state: Uint8Array } | null>(null)

  // ---- load the city snapshot (streamed so we can show progress)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch('/api/sim/snapshot.bin')
        if (!r.ok || !r.body) throw new Error(`The API returned ${r.status}. Is the generator finished?`)
        const reader = r.body.getReader()
        const chunks: Uint8Array[] = []
        let got = 0
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          chunks.push(value); got += value.length
          if (!cancelled) setProgress(`Loading residents… ${(got / 1e6).toFixed(0)} MB`)
        }
        const all = new Uint8Array(got)
        let o = 0
        for (const c of chunks) { all.set(c, o); o += c.length }
        const s = decode(all.buffer)
        if (!cancelled) {
          setBufs({ pos: new Float32Array(2 * s.n), col: new Uint8Array(4 * s.n), state: new Uint8Array(s.n) })
          setSnap(s)
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ---- recompute positions/colours whenever the clock or colour mode changes.
  // Writes into the preallocated typed arrays; the returned token tells deck.gl to re-upload them.
  const frame = useMemo(() => {
    if (!snap || !bufs) return 0
    positionsAt(snap, minute, bufs.pos, bufs.state)
    paint(snap, colourBy, bufs.state, bufs.col)
    return minute + 1440 * ['activity', 'signature', 'mode', 'age'].indexOf(colourBy) + 1
  }, [snap, bufs, minute, colourBy])

  // ---- playback clock
  useEffect(() => {
    if (!playing) return
    let raf = 0, last = performance.now()
    const tick = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      setMinute((m) => (m + dt * speed) % 1440)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed])

  // ---- selected person
  const select = useCallback((id: number | null, fly = false) => {
    setSelected(id)
    setPerson(null)
    setRoute(null)
    if (id == null) return
    api.person(id).then((p) => {
      setPerson(p)
      if (!fly) return
      // frame the whole day (home, work, errands) between the side panels
      const xs = p.schedule.map((s) => s.lon), ys = p.schedule.map((s) => s.lat)
      const pad = 0.002
      const vp = new WebMercatorViewport({ width: window.innerWidth, height: window.innerHeight })
      const fit = vp.fitBounds([[Math.min(...xs) - pad, Math.min(...ys) - pad], [Math.max(...xs) + pad, Math.max(...ys) + pad]],
        { padding: window.innerWidth > 1100 ? { left: 380, right: 450, top: 80, bottom: 130 } : 40 })
      setViewState((v) => ({ ...v, longitude: fit.longitude, latitude: fit.latitude, zoom: Math.min(fit.zoom, 16), transitionDuration: 1200 }))
    }).catch((e) => setError(e.message))
    api.route(id).then(setRoute).catch(() => setRoute(null))
  }, [])

  // ---- deep link: /?person=123 opens that record
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const id = Number(q.get('person'))
    if (!(id > 0)) return
    const h = setTimeout(() => select(id, true), 0)
    return () => clearTimeout(h)
  }, [select])

  useEffect(() => {
    const url = new URL(window.location.href)
    if (selected != null) url.searchParams.set('person', String(selected))
    else url.searchParams.delete('person')
    window.history.replaceState(null, '', url)
  }, [selected])

  const onClick = useCallback((info: PickingInfo) => {
    if (info.layer?.id === 'people' && info.index >= 0) select(info.index + 1)
  }, [select])

  const layers = useMemo(() => {
    if (!snap || !bufs) return []
    const b = bufs
    const out: unknown[] = [
      new ScatterplotLayer({
        id: 'people',
        data: { length: snap.n, attributes: { getPosition: { value: b.pos, size: 2 }, getFillColor: { value: b.col, size: 4 } } },
        radiusUnits: 'meters',
        getRadius: 6,
        radiusMinPixels: 0.7,
        radiusMaxPixels: 6,
        pickable: true,
        updateTriggers: { getPosition: frame, getFillColor: frame },
        parameters: { depthTest: false },
      }),
    ]
    if (route) {
      out.push(new GeoJsonLayer({
        id: 'route', data: route, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 4,
        getLineColor: [201, 162, 39, 230], lineCapRounded: true, lineJointRounded: true,
      }))
    }
    if (selected != null) {
      const i = selected - 1
      const stops = person?.schedule ?? []
      out.push(new ScatterplotLayer({
        id: 'stops', data: stops, getPosition: (d: { lon: number; lat: number }) => [d.lon, d.lat],
        getRadius: 9, radiusUnits: 'pixels', stroked: true, filled: true, getFillColor: [19, 36, 48, 255],
        getLineColor: [201, 162, 39, 255], lineWidthMinPixels: 2,
      }))
      out.push(new ScatterplotLayer({
        id: 'selected', data: [i], getPosition: () => [b.pos[i * 2], b.pos[i * 2 + 1]],
        getRadius: 11, radiusUnits: 'pixels', stroked: true, filled: true,
        getFillColor: () => [snap.colour[i * 4], snap.colour[i * 4 + 1], snap.colour[i * 4 + 2], 255],
        getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 3,
        updateTriggers: { getPosition: frame },
      }))
    }
    return out
  }, [snap, bufs, frame, route, selected, person])

  return (
    <div className="map-page">
      <DeckGL
        viewState={viewState}
        onViewStateChange={({ viewState: v }) => setViewState(v as MapViewState)}
        controller
        layers={layers as never}
        onClick={onClick}
        getCursor={({ isHovering }) => (isHovering ? 'pointer' : 'grab')}
      >
        <Map mapStyle={BASEMAP} />
      </DeckGL>

      <aside className="rail">
        <h1 className="brand">One million people in Birmingham</h1>
        <p className="lede">
          Every dot is a synthetic resident with their own record, home and working day. Press play to watch the city
          wake up, or select any dot to open that person's record.
        </p>
        <Search onPick={(id) => select(id, true)} />
        <fieldset className="colour-by">
          <legend>Colour dots by</legend>
          {(['activity', 'signature', 'mode', 'age'] as ColourBy[]).map((c) => (
            <label key={c} className={colourBy === c ? 'on' : ''}>
              <input type="radio" name="colourBy" checked={colourBy === c} onChange={() => setColourBy(c)} />
              {{ activity: 'What they’re doing', signature: 'Their own colour', mode: 'How they travel', age: 'Age' }[c]}
            </label>
          ))}
        </fieldset>
        {colourBy === 'signature' ? (
          <p className="hint">Each resident has a unique colour. No two of the million share one.</p>
        ) : (
          <ul className="legend">
            {LEGENDS[colourBy].map((l) => (
              <li key={l.label}><i style={{ background: `rgb(${l.rgb.join(',')})` }} />{l.label}</li>
            ))}
          </ul>
        )}
        {!snap && !error && <p className="status">{progress}</p>}
        {error && <p className="status error">{error}</p>}
      </aside>

      <TimeBar minute={minute} setMinute={setMinute} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} />

      {selected != null && (
        <PersonPanel person={person} route={route} onClose={() => select(null)} onSelect={(id) => select(id, true)} />
      )}
    </div>
  )
}
