import { useEffect, useState } from 'react'
import { api, type Member } from '../api'

export function Search({ onPick }: { onPick: (id: number) => void }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Member[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (q.trim().length < 2) return
    const h = setTimeout(() => {
      api.search(q).then((r) => { setResults(r); setOpen(true) }).catch(() => setResults([]))
    }, 220)
    return () => clearTimeout(h)
  }, [q])

  return (
    <div className="search">
      <label htmlFor="search-input">Find a resident</label>
      <input
        id="search-input" type="search" value={q} autoComplete="off"
        placeholder="Name, citizen ID or number"
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && q.trim().length >= 2 && results.length > 0 && (
        <ul className="results" role="listbox">
          {results.map((r) => (
            <li key={r.person_id}>
              <button onMouseDown={() => { onPick(r.person_id); setOpen(false) }}>
                <i style={{ background: r.signature_colour }} />
                <span>{r.first_name} {r.last_name}</span>
                {r.age != null && <small>{r.age}</small>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && q.trim().length >= 2 && results.length === 0 && <p className="hint">No residents match “{q}”.</p>}
    </div>
  )
}
