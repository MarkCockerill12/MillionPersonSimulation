import { useEffect, useState } from 'react'
import { api, type Analysis, type Meta } from '../api'
import { Chart, DataTable } from '../components/Chart'

const n = new Intl.NumberFormat('en-GB')

function Card({ id }: { id: string }) {
  const [a, setA] = useState<Analysis | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [view, setView] = useState<'chart' | 'table' | 'sql'>('chart')
  useEffect(() => { api.analysis(id).then(setA).catch((e) => setErr(e.message)) }, [id])

  return (
    <article className="card">
      <header>
        <h2>{a?.title ?? ' '}</h2>
        <p>{a?.description}</p>
      </header>
      <div className="tabs" role="tablist">
        {(a?.chart === 'table' ? ['table', 'sql'] : ['chart', 'table', 'sql']).map((v) => (
          <button key={v} role="tab" aria-selected={view === v || (a?.chart === 'table' && v === 'table' && view === 'chart')}
            onClick={() => setView(v as typeof view)}>
            {{ chart: 'Chart', table: 'Table', sql: 'SQL' }[v]}
          </button>
        ))}
        {a?.elapsed_ms != null && <span className="elapsed num">Ran in {n.format(a.elapsed_ms)} ms</span>}
      </div>
      <div className="body">
        {err && <p className="status error">{err}</p>}
        {!a && !err && <p className="status">Running query…</p>}
        {a && view === 'chart' && <Chart a={a} />}
        {a && view === 'table' && <DataTable a={a} />}
        {a && view === 'sql' && <pre className="sql"><code>{a.sql}</code></pre>}
      </div>
    </article>
  )
}

export default function Analytics() {
  const [list, setList] = useState<Analysis[]>([])
  const [meta, setMeta] = useState<Meta | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    api.analyses().then(setList).catch((e) => setErr(e.message))
    api.meta().then(setMeta).catch(() => null)
  }, [])

  return (
    <main className="page analytics">
      <header className="page-head">
        <h1>What the database says about the city</h1>
        <p>
          Every panel is one SQL query from <code>sql/analysis/</code>, run live against PostgreSQL. Open the SQL tab to read it.
        </p>
        {meta && (
          <dl className="totals">
            <div><dt>Residents</dt><dd>{n.format(meta.people)}</dd></div>
            <div><dt>Households</dt><dd>{n.format(meta.households)}</dd></div>
            <div><dt>Family links</dt><dd>{n.format(meta.relationships)}</dd></div>
            <div><dt>Daily activities</dt><dd>{n.format(meta.activities)}</dd></div>
            <div><dt>Places</dt><dd>{n.format(meta.places)}</dd></div>
            <div><dt>Database size</dt><dd>{meta.db_size}</dd></div>
          </dl>
        )}
      </header>
      {err && <p className="status error">{err}</p>}
      <div className="grid">
        {list.map((a) => <Card key={a.id} id={a.id} />)}
      </div>
    </main>
  )
}
