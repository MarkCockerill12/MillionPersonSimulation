import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { label, type Analysis } from '../api'

// Categorical palette (dark steps), validated against the #16252f chart surface. Fixed order, never cycled.
const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767']
const INK = '#c9d3d6'
const MUTED = '#7f9299'
const GRID = 'rgba(201, 211, 214, 0.10)'

const compact = new Intl.NumberFormat('en-GB', { notation: 'compact', maximumFractionDigits: 1 })
const full = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 })

const axis = { stroke: MUTED, tick: { fill: MUTED, fontSize: 12 }, tickLine: false, axisLine: { stroke: GRID } }
const tooltip = {
  contentStyle: { background: '#0f1c24', border: '1px solid rgba(201,211,214,.18)', borderRadius: 6, color: INK, fontSize: 13 },
  labelStyle: { color: INK, fontWeight: 600 },
  itemStyle: { color: INK },
  cursor: { fill: 'rgba(201,211,214,.06)', stroke: 'rgba(201,211,214,.3)' },
}

// Category tick that wraps onto two lines instead of colliding with its neighbours.
function WrapTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  const words = label(String(payload?.value ?? '')).split(' ')
  const mid = Math.ceil(words.length / 2)
  const lines = words.length > 1 ? [words.slice(0, mid).join(' '), words.slice(mid).join(' ')] : words
  return (
    <text x={x} y={(y ?? 0) + 12} textAnchor="middle" fill={MUTED} fontSize={12}>
      {lines.map((l, i) => <tspan key={i} x={x} dy={i ? 14 : 0}>{l}</tspan>)}
    </text>
  )
}

function toObjects(a: Analysis) {
  const cols = a.columns ?? []
  return (a.rows ?? []).map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])))
}

export function Chart({ a }: { a: Analysis }) {
  const data = toObjects(a)
  const x = a.x ?? a.columns?.[0] ?? ''
  const ys = (a.y ?? '').split(',').map((s) => s.trim()).filter(Boolean)
  const legend = ys.length > 1 ? <Legend iconType="circle" iconSize={8} wrapperStyle={{ color: INK, fontSize: 12 }} formatter={(v) => <span style={{ color: INK }}>{label(String(v))}</span>} /> : null
  const fmt = (v: unknown) => (typeof v === 'number' ? full.format(Math.abs(v)) : String(v))

  if (a.chart === 'pyramid') {
    return (
      <ResponsiveContainer width="100%" height={380}>
        <BarChart data={data} layout="vertical" stackOffset="sign" barCategoryGap={2}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" {...axis} tickFormatter={(v) => compact.format(Math.abs(v))} />
          <YAxis type="category" dataKey={x} {...axis} width={52} interval={0} fontSize={11} />
          <Tooltip {...tooltip} formatter={(v, n) => [fmt(v), label(String(n))]} />
          {legend}
          <Bar dataKey="female" stackId="s" fill={SERIES[0]} radius={[0, 4, 4, 0]} />
          <Bar dataKey="male" stackId="s" fill={SERIES[1]} radius={[4, 0, 0, 4]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }
  if (a.chart === 'bar') {
    return (
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey={x} {...axis} tick={<WrapTick />} interval={0} height={40} />
          <YAxis {...axis} tickFormatter={(v) => compact.format(v)} width={48} />
          <Tooltip {...tooltip} labelFormatter={(v) => label(String(v))} formatter={(v, n) => [fmt(v), label(String(n))]} />
          {ys.map((y, i) => <Bar key={y} dataKey={y} fill={SERIES[i]} radius={[4, 4, 0, 0]} />)}
        </BarChart>
      </ResponsiveContainer>
    )
  }
  if (a.chart === 'stacked') {
    return (
      <ResponsiveContainer width="100%" height={320}>
        <AreaChart data={data}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey={x} {...axis} minTickGap={24} />
          <YAxis {...axis} tickFormatter={(v) => compact.format(v)} width={48} />
          <Tooltip {...tooltip} formatter={(v, n) => [fmt(v), label(String(n))]} />
          {legend}
          {ys.map((y, i) => (
            <Area key={y} type="monotone" dataKey={y} stackId="1" stroke="#16252f" strokeWidth={1.5}
              fill={SERIES[i]} fillOpacity={0.92} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    )
  }
  if (a.chart === 'line') {
    return (
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey={x} {...axis} />
          <YAxis {...axis} tickFormatter={(v) => `£${compact.format(v)}`} width={52} />
          <Tooltip {...tooltip} formatter={(v, n) => [`£${fmt(v)}`, label(String(n))]} />
          {legend}
          {ys.map((y, i) => <Line key={y} type="monotone" dataKey={y} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 4, strokeWidth: 0, fill: SERIES[i] }} />)}
        </LineChart>
      </ResponsiveContainer>
    )
  }
  if (a.chart === 'range') {
    // Floating bar from p25 to p75, median as the bar's end marker via tooltip.
    const rows = data.map((d) => ({ ...d, iqr: [d.p25, d.p75] }))
    return (
      <ResponsiveContainer width="100%" height={Math.max(320, rows.length * 18)}>
        <BarChart data={rows} layout="vertical" barCategoryGap={3}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" {...axis} tickFormatter={(v) => `£${compact.format(v)}`} />
          <YAxis type="category" dataKey={x} {...axis} width={170} interval={0} fontSize={11} />
          <Tooltip {...tooltip} formatter={(_v, _n, item) => {
            const d = item.payload as Record<string, number>
            return [`£${full.format(d.p25)} to £${full.format(d.p75)} (median £${full.format(d.median)})`, 'Middle 50%']
          }} />
          <Bar dataKey="iqr" fill={SERIES[0]} radius={4} />
        </BarChart>
      </ResponsiveContainer>
    )
  }
  return <DataTable a={a} />
}

export function DataTable({ a }: { a: Analysis }) {
  const cols = a.columns ?? []
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{label(c)}</th>)}</tr></thead>
        <tbody>
          {(a.rows ?? []).map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{typeof v === 'number' ? full.format(v) : String(v ?? '')}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
