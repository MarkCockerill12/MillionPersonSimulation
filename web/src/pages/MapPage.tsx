import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MapboxOverlay, type MapboxOverlayProps } from '@deck.gl/mapbox'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import { Map, useControl, type MapLayerMouseEvent, type MapRef } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { StyleSpecification } from 'maplibre-gl'
import { api, type Meta, type Person } from '../api'
import { alongLine, decodeChunk, legAt, positionOf, type Chunk, type Manifest } from '../sim'
import { SimLayer, type ColourMode } from '../layers/SimLayer'
import { PersonPanel } from '../components/PersonPanel'
import { TimeBar } from '../components/TimeBar'
import { Search } from '../components/Search'
import { PerfReadout } from '../components/PerfReadout'


const QS = new URLSearchParams(window.location.search)
const DEBUG = QS.has('debug')
// performance experiments (debug only): ?debug=1 plus &nobasemap=1 / &nopeople=1 / &nolod=1 / &fullstyle=1
const NO_BASEMAP = DEBUG && QS.has('nobasemap')
const NO_PEOPLE = DEBUG && QS.has('nopeople')
const NO_LOD = DEBUG && QS.has('nolod')

const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
// CARTO Dark Matter, trimmed. The full style draws 95 layers per tile (16 tunnel layers, casings for every road
// class, buildings, house numbers…); behind a million dots most of that is invisible but every layer is a draw
// call per tile per frame. Keeping ~30 halves the basemap's cost while panning. ?debug=1&fullstyle=1 compares.
const KEEP_LAYER = /^(background|landcover|park_|landuse|water$|waterway$|road_(minor|sec|pri|trunk|mot)_fill|bridge_(minor|sec|pri|trunk|mot)_fill|rail$|waterway_label|watername_lake|place_(hamlet|suburbs|villages|town|city)|roadname_(sec|pri|major))/
async function loadBasemapStyle(full: boolean): Promise<StyleSpecification> {
  const style: StyleSpecification = await fetch(BASEMAP).then((r) => r.json())
  if (!full) style.layers = style.layers.filter((l: { id: string }) => KEEP_LAYER.test(l.id))
  return style
}
const EMPTY_STYLE = { version: 8 as const, sources: {}, layers: [{ id: 'bg', type: 'background' as const, paint: { 'background-color': '#0e1a22' } }] }

export type ColourBy = 'activity' | 'signature' | 'mode' | 'age'
const COLOUR_MODE: Record<ColourBy, ColourMode> = { activity: 0, signature: 1, mode: 2, age: 3 }

type RGB = [number, number, number]
const legends = (modeLabels: Record<string, string>): Record<ColourBy, { label: string; rgb: RGB }[]> => ({
  signature: [],
  activity: [
    { label: 'Home', rgb: [74, 110, 140] }, { label: 'Work', rgb: [222, 178, 60] },
    { label: 'School or university', rgb: [95, 184, 176] }, { label: 'Shopping or eating out', rgb: [224, 122, 155] },
    { label: 'Leisure', rgb: [150, 130, 230] }, { label: 'Healthcare', rgb: [235, 96, 80] }, { label: 'Travelling', rgb: [245, 245, 240] },
  ],
  mode: [
    { label: modeLabels.car ?? 'Car', rgb: [235, 110, 90] }, { label: modeLabels.bus ?? 'Bus', rgb: [222, 178, 60] },
    { label: modeLabels.train ?? 'Train', rgb: [150, 130, 230] }, { label: modeLabels.cycle ?? 'Cycle', rgb: [95, 184, 176] },
    { label: modeLabels.walk ?? 'Walk', rgb: [155, 211, 122] }, { label: 'Stays home', rgb: [70, 84, 96] },
  ],
  age: [
    { label: '0–17', rgb: [95, 184, 176] }, { label: '18–39', rgb: [155, 211, 122] },
    { label: '40–64', rgb: [222, 178, 60] }, { label: '65+', rgb: [235, 110, 90] },
  ],
})

// deck.gl drawn *inside* MapLibre's WebGL context (interleaved): one context, one canvas, one render loop,
// and MapLibre owns the camera, so dragging never touches React. Automatic picking is off (pickAsync: false):
// deck would otherwise re-render every resident into a picking buffer and read it back on every mouse move.
// We pick explicitly on click, and on hover only once the mouse has paused.
function DeckOverlay(props: MapboxOverlayProps & { onReady: (o: MapboxOverlay) => void }) {
  const { onReady, ...rest } = props
  const overlay = useControl<MapboxOverlay>(() => new MapboxOverlay({ ...rest, interleaved: true, pickAsync: false } as never))
  overlay.setProps({ ...rest, pickAsync: false } as never)
  useEffect(() => onReady(overlay), [overlay, onReady])
  return null
}

const FIT_PADDING = () => (window.innerWidth > 1100 ? { left: 380, right: 450, top: 80, bottom: 130 } : 40)

export interface LoadStats { people: number; bytes: number; ms: number | null; chunks: number; total: number }

const MemoPanel = memo(PersonPanel)

export default function MapPage() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [load, setLoad] = useState<LoadStats>({ people: 0, bytes: 0, ms: null, chunks: 0, total: 0 })
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
  const [routeMs, setRouteMs] = useState<number | null>(null)
  const [initialView, setInitialView] = useState<{ longitude: number; latitude: number; zoom: number } | null>(null)
  const mapRef = useRef<MapRef>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const hoverTimer = useRef<number | undefined>(undefined)
  const movingRef = useRef(false)  // camera in motion (drag, zoom, fly-to)
  const pendingFit = useRef<[[number, number], [number, number]] | null>(null)

  // ---- city metadata + snapshot chunks, fetched in parallel; each chunk renders the moment it lands
  useEffect(() => {
    let cancelled = false
    const t0 = performance.now()
    api.meta().then((m) => {
      if (cancelled) return
      setMeta(m)
      const [x0, y0, x1, y1] = m.bbox
      setInitialView((v) => v ?? { longitude: (x0 + x1) / 2, latitude: (y0 + y1) / 2, zoom: 11 })
    }).catch((e) => !cancelled && setError(e.message))
    ;(async () => {
      try {
        const r = await fetch('/api/sim/manifest.json')
        if (!r.ok) throw new Error('No map snapshot yet. Run the generator first.')
        const man: Manifest = await r.json()
        if (cancelled) return
        setManifest(man)
        // the snapshot alone is enough to show the map, even if /api/meta is slow or down
        setInitialView((v) => v ?? { longitude: (man.bbox[0] + man.bbox[2]) / 2, latitude: (man.bbox[1] + man.bbox[3]) / 2, zoom: 11 })
        setLoad((l) => ({ ...l, total: man.chunks.length }))
        await Promise.all(man.chunks.map(async (c) => {
          const res = await fetch(c.url)
          const ch = decodeChunk(await res.arrayBuffer())
          if (cancelled) return
          setChunks((cs) => [...cs, ch].sort((a, b) => a.start - b.start))
          setLoad((l) => ({ ...l, people: l.people + ch.count, bytes: l.bytes + c.bytes, chunks: l.chunks + 1 }))
        }))
        if (!cancelled) setLoad((l) => ({ ...l, ms: performance.now() - t0 }))
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ---- playback clock (the GPU does the rest)
  useEffect(() => {
    if (!playing) return
    let raf = 0, last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      setMinute((m) => (m + dt * speed) % 1440)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed])

  // ---- selected person: record + road route fetched in parallel
  const select = useCallback((id: number | null, fly = false) => {
    setSelected(id)
    setPerson(null)
    setRoute(null)
    setRouteMs(null)
    if (id == null) return
    api.person(id).then((p) => {
      setPerson(p)
      if (!fly) return
      const xs = p.schedule.map((s) => s.lon), ys = p.schedule.map((s) => s.lat)
      const pad = 0.002
      // frame the whole day (home, work, errands) between the side panels; MapLibre animates natively
      const bounds: [[number, number], [number, number]] = [[Math.min(...xs) - pad, Math.min(...ys) - pad], [Math.max(...xs) + pad, Math.max(...ys) + pad]]
      // a deep-linked record can arrive before the map exists: park the camera move until it loads
      if (mapRef.current) mapRef.current.fitBounds(bounds, { padding: FIT_PADDING(), maxZoom: 16, duration: 900 })
      else pendingFit.current = bounds
    }).catch((e) => setError(e.message))
    const t = performance.now()
    api.route(id).then((r) => { setRoute(r); setRouteMs(performance.now() - t) }).catch(() => setRoute(null))
  }, [])
  const close = useCallback(() => select(null), [select])
  const openRelated = useCallback((id: number) => select(id, true), [select])

  // ---- deep link: /?person=123 opens that record
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get('person'))
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

  const pickAt = useCallback((x: number, y: number): PickingInfo | null =>
    overlayRef.current?.pickObject({ x, y, radius: 6 }) ?? null, [])

  const onMapClick = useCallback((e: MapLayerMouseEvent) => {
    const info = pickAt(e.point.x, e.point.y)
    const ch = (info?.layer?.props as { chunkStart?: number } | undefined)?.chunkStart
    if (info && ch != null && info.index >= 0) select(ch + info.index + 1)
  }, [pickAt, select])

  // hover affordance without per-frame picking: pick once the pointer has rested for 120 ms, and never while
  // a button is held or the camera is moving (each pick is a synchronous GPU read-back)
  const onMapMouseMove = useCallback((e: MapLayerMouseEvent) => {
    window.clearTimeout(hoverTimer.current)
    if (e.originalEvent.buttons !== 0 || movingRef.current) return
    const canvas = e.target.getCanvas()
    if (canvas.style.cursor === 'pointer') canvas.style.cursor = ''
    hoverTimer.current = window.setTimeout(() => {
      const info = pickAt(e.point.x, e.point.y)
      canvas.style.cursor = info?.layer?.props && (info.layer.props as { chunkStart?: number }).chunkStart != null ? 'pointer' : ''
    }, 120)
  }, [pickAt])

  // Level of detail while the camera moves: zoomed out, a million dots overlap heavily (most pixels are painted
  // many times), so drawing a uniform 50% sample during a drag looks the same and halves the overdraw.
  // Everyone is drawn again the moment the camera stops. Zoomed in, all residents are always drawn.
  const [motionLod, setMotionLod] = useState(false)
  const onMoveStart = useCallback((e: { target: { getZoom: () => number } }) => {
    movingRef.current = true
    window.clearTimeout(hoverTimer.current)
    if (!NO_LOD && e.target.getZoom() < 13) setMotionLod(true)
  }, [])
  const onMoveEnd = useCallback(() => { movingRef.current = false; setMotionLod(false) }, [])

  const [basemapStyle, setBasemapStyle] = useState<StyleSpecification | null>(null)
  useEffect(() => {
    loadBasemapStyle(DEBUG && QS.has('fullstyle')).then(setBasemapStyle).catch(() => setBasemapStyle(null))
  }, [])

  // draw residents beneath the basemap's place labels so names stay readable
  const [labelLayer, setLabelLayer] = useState<string | undefined>(undefined)
  const onMapLoad = useCallback(() => {
    if (pendingFit.current) {
      mapRef.current?.fitBounds(pendingFit.current, { padding: FIT_PADDING(), maxZoom: 16, duration: 900 })
      pendingFit.current = null
    }
  }, [])
  const onStyleLoad = useCallback(() => {
    const layers = mapRef.current?.getMap().getStyle().layers ?? []
    setLabelLayer(layers.find((l) => l.type === 'symbol')?.id)
  }, [])

  // Warm up deck's picking framebuffer while the browser is idle, so the first click or hover doesn't pay for
  // allocating it (and luma.gl's format-capability probe, which uses synchronous gl.getError calls).
  const chunkCount = chunks.length
  useEffect(() => {
    if (!chunkCount || !overlayRef.current) return
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 200))
    const h = idle(() => { overlayRef.current?.pickObject({ x: 1, y: 1, radius: 0 }) })
    return () => (window.cancelIdleCallback ?? window.clearTimeout)(h)
  }, [chunkCount])

  const onOverlayReady = useCallback((o: MapboxOverlay) => {
    overlayRef.current = o
    if (import.meta.env.DEV || DEBUG) Object.assign(window, { __overlay: o, __map: mapRef.current?.getMap() })
  }, [])

  // ---- layers: one GPU sim layer per chunk; only `time` / `colourMode` change per frame
  const bbox = manifest?.bbox
  // Benchmark: ?scale=N draws N copies of the population (a few metres apart) to measure GPU headroom.
  const [scale] = useState(() => Math.min(20, Math.max(1, Math.floor(Number(new URLSearchParams(window.location.search).get('scale')) || 1))))
  const peopleLayers = useMemo(() => !bbox ? [] : Array.from({ length: scale }, (_, copy) => chunks.map((ch) => new SimLayer({
    id: `people-${copy}-${ch.start}`,
    data: ch.data,
    chunkStart: copy === 0 ? ch.start : undefined,
    bbox: copy === 0 ? bbox : bbox.map((v, i) => v + (i % 2 ? 0.00025 : 0.0004) * Math.sin(copy * (i + 1))) as typeof bbox,
    time: minute,
    colourMode: COLOUR_MODE[colourBy],
    radiusMeters: 6,
    radiusMinPixels: 0.7,
    radiusMaxPixels: 6,
    pickable: copy === 0,
    beforeId: labelLayer,
    drawFraction: motionLod ? 0.5 : 1,
    // opaque dots: no depth test and no blending, so overlapping people cost a plain write instead of a read-modify-write
    parameters: { depthWriteEnabled: false, depthCompare: 'always', blend: false },
  } as never))).flat(), [chunks, bbox, minute, colourBy, scale, labelLayer, motionLod])

  const overlay = useMemo(() => {
    const out: unknown[] = []
    if (route) {
      out.push(new GeoJsonLayer({
        id: 'route', data: route, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 4,
        getLineColor: [201, 162, 39, 230], lineCapRounded: true, lineJointRounded: true,
      }))
    }
    if (person) {
      out.push(new ScatterplotLayer({
        id: 'stops', data: person.schedule, getPosition: (d: { lon: number; lat: number }) => [d.lon, d.lat],
        getRadius: 9, radiusUnits: 'pixels', stroked: true, filled: true, getFillColor: [19, 36, 48, 255],
        getLineColor: [201, 162, 39, 255], lineWidthMinPixels: 2,
      }))
    }
    if (selected != null && bbox) {
      const i = selected - 1
      const ch = chunks.find((c) => i >= c.start && i < c.start + c.count)
      if (ch) {
        const k = i - ch.start
        // the selected person rides their real road route; everyone else moves in a straight line on the GPU
        const leg = legAt(ch, k, minute)
        const road = leg && route?.features.find((f) => f.properties?.leg === leg.leg)
        const pos = road && road.geometry.type === 'LineString'
          ? alongLine(road.geometry.coordinates, leg.f)
          : positionOf(ch, k, minute, bbox)
        out.push(new ScatterplotLayer({
          id: 'selected', data: [pos], getPosition: (d: [number, number]) => d,
          getRadius: 11, radiusUnits: 'pixels', stroked: true, filled: true,
          getFillColor: [ch.colour[k * 4], ch.colour[k * 4 + 1], ch.colour[k * 4 + 2], 255],
          getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 3,
          updateTriggers: { getPosition: minute },
        }))
      }
    }
    return out
  }, [route, person, selected, chunks, bbox, minute])

  const cityName = meta?.city?.name ?? '…'
  const legend = legends(meta?.city?.mode_labels ?? {})

  return (
    <div className="map-page">
      {initialView && (
        <Map
          ref={mapRef}
          initialViewState={initialView}
          mapStyle={NO_BASEMAP ? EMPTY_STYLE : basemapStyle ?? EMPTY_STYLE}
          onClick={onMapClick}
          onMouseMove={onMapMouseMove}
          onLoad={onMapLoad}
          onStyleData={onStyleLoad}
          onMoveStart={onMoveStart}
          onMoveEnd={onMoveEnd}
          dragRotate={false}
          renderWorldCopies={false}
          pitchWithRotate={false}
          touchPitch={false}
          style={{ position: 'absolute', inset: 0 }}
        >
          <DeckOverlay layers={[...(NO_PEOPLE ? [] : peopleLayers), ...overlay] as never} onReady={onOverlayReady} />
        </Map>
      )}

      <aside className="rail">
        <h1 className="brand">One million people in {cityName}</h1>
        <p className="lede">
          Every dot is a synthetic resident with their own record, home and working day. Press play to watch the city
          wake up, or select any dot to open that person's record.
        </p>
        <Search onPick={openRelated} />
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
            {legend[colourBy].map((l) => (
              <li key={l.label}><i style={{ background: `rgb(${l.rgb.join(',')})` }} />{l.label}</li>
            ))}
          </ul>
        )}
        {error && <p className="status error">{error}</p>}
        <PerfReadout load={load} routeMs={routeMs} scale={scale} />
      </aside>

      <TimeBar minute={minute} setMinute={setMinute} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} />

      {selected != null && (
        <MemoPanel person={person} route={route} currency={meta?.city?.currency ?? 'EUR'}
          modeLabels={meta?.city?.mode_labels ?? {}} onClose={close} onSelect={openRelated} />
      )}
    </div>
  )
}
