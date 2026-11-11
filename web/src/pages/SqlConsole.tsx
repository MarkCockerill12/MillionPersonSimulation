import { useEffect, useState } from 'react'
import { api } from '../api'

const STARTER = `-- Read-only. Try editing this and pressing Ctrl+Enter.
SELECT d.name AS district,
       count(*) AS residents,
       round(avg(p.annual_income)) AS avg_income
FROM persons p
JOIN households h USING (household_id)
JOIN districts d  USING (district_id)
GROUP BY d.name
ORDER BY residents DESC
LIMIT 20;`

const EXAMPLES: { name: string; sql: string }[] = [
  { name: 'A random family tree', sql: `-- Three generations around one grandparent\nWITH g AS (\n  SELECT person_id FROM relationships WHERE relationship = 'grandparent' LIMIT 1\n)\nSELECT r.relationship, p.first_name, p.last_name,\n       date_part('year', age(DATE '2026-01-01', p.date_of_birth)) AS age,\n       h.street, h.postcode\nFROM g\nJOIN relationships r ON r.person_id = g.person_id\nJOIN persons p ON p.person_id = r.related_person_id\nJOIN households h USING (household_id)\nORDER BY r.relationship;` },
  { name: 'Busiest place at 12:00', sql: `SELECT pl.name, pl.category, count(*) AS people_there\nFROM activities a\nJOIN places pl USING (place_id)\nWHERE 720 BETWEEN a.start_min AND a.end_min\nGROUP BY pl.place_id\nORDER BY people_there DESC\nLIMIT 15;` },
  { name: 'Neighbours within 100 m', sql: `-- Spatial KNN join (PostGIS) around person #1\nSELECT p.first_name, p.last_name,\n       round(ST_Distance(h.geom::geography, me.geom::geography)) AS metres\nFROM households me\nJOIN households h ON ST_DWithin(h.geom::geography, me.geom::geography, 100)\nJOIN persons p ON p.household_id = h.household_id\nWHERE me.household_id = (SELECT household_id FROM persons WHERE person_id = 1)\n  AND h.household_id <> me.household_id\nORDER BY metres\nLIMIT 25;` },
  { name: 'Road route with pgRouting', sql: `-- Shortest drive between two random junctions, computed inside PostgreSQL\nWITH pair AS (\n  SELECT (SELECT node_id FROM road_nodes ORDER BY random() LIMIT 1) AS a,\n         (SELECT node_id FROM road_nodes ORDER BY random() LIMIT 1) AS b\n)\nSELECT r.seq, e.name AS street, round(e.length_m) AS metres, round(r.agg_cost) AS seconds_so_far\nFROM pair,\n     pgr_dijkstra('SELECT edge_id AS id, source, target, cost_s AS cost, reverse_cost_s AS reverse_cost FROM city.road_edges',\n                  pair.a, pair.b, directed := true) r\nJOIN road_edges e ON e.edge_id = r.edge\nORDER BY r.seq;` },
  { name: 'Query plan', sql: `EXPLAIN ANALYZE\nSELECT * FROM persons WHERE (first_name || ' ' || last_name) ILIKE '%smith%' LIMIT 10;` },
]

type Result = Awaited<ReturnType<typeof api.sql>>

export default function SqlConsole() {
  const [sql, setSql] = useState(STARTER)
  const [res, setRes] = useState<Result | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [schema, setSchema] = useState<Record<string, { name: string; type: string }[]>>({})

  useEffect(() => { api.schema().then(setSchema).catch(() => null) }, [])

  const run = async () => {
    setBusy(true); setErr(null)
    try { setRes(await api.sql(sql)) } catch (e) { setErr((e as Error).message); setRes(null) } finally { setBusy(false) }
  }

  return (
    <main className="page console">
      <aside className="schema">
        <h2>Tables</h2>
        {Object.entries(schema).map(([t, cols]) => (
          <details key={t}>
            <summary>{t}</summary>
            <ul>{cols.map((c) => <li key={c.name}><span>{c.name}</span><small>{c.type}</small></li>)}</ul>
          </details>
        ))}
        <h2>Examples</h2>
        <ul className="examples">
          {EXAMPLES.map((e) => <li key={e.name}><button onClick={() => setSql(e.sql)}>{e.name}</button></li>)}
        </ul>
      </aside>
      <section className="editor">
        <textarea
          value={sql} spellCheck={false} aria-label="SQL query"
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); run() } }}
        />
        <div className="run-row">
          <button className="primary" onClick={run} disabled={busy}>{busy ? 'Running…' : 'Run query'}</button>
          <span className="hint">Runs as the read-only <code>analyst</code> role, with a 15-second limit and up to 1,000 rows.</span>
          {res && <span className="elapsed num">{res.row_count} rows{res.truncated ? ' (truncated)' : ''} in {res.elapsed_ms} ms</span>}
        </div>
        {err && <pre className="status error">{err}</pre>}
        {res && (
          <div className="table-wrap">
            <table>
              <thead><tr>{res.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{res.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{v === null ? <em>null</em> : String(v)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
