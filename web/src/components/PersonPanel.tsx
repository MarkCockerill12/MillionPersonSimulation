import { label, type Person } from '../api'
import { fmtTime } from '../sim'

interface Props {
  person: Person | null
  route: GeoJSON.FeatureCollection | null
  onClose: () => void
  onSelect: (id: number) => void
}

const gbp = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 })

function address(p: Person) {
  return `${p.flat_number ? `Flat ${p.flat_number}, ` : ''}${p.house_number} ${p.street}`
}

export function PersonPanel({ person: p, route, onClose, onSelect }: Props) {
  const km = route?.features.reduce((s, f) => s + Number(f.properties?.length_m ?? 0), 0) ?? 0
  return (
    <aside className="record" aria-label="Resident record">
      <button className="close" onClick={onClose} aria-label="Close record">×</button>
      {!p ? <p className="status">Opening record…</p> : (
        <>
          <header className="record-head">
            <span className="swatch" style={{ background: p.signature_colour }} aria-hidden />
            <div>
              <h2>{p.first_name} {p.middle_name ? `${p.middle_name} ` : ''}{p.last_name}</h2>
              <p className="sub">{p.age} · {label(p.employment_status)}{p.occupation ? `, ${p.occupation}` : ''}</p>
            </div>
          </header>

          <dl className="facts">
            <div><dt>Citizen ID</dt><dd className="num">{p.citizen_id}</dd></div>
            <div><dt>Born</dt><dd className="num">{new Date(p.date_of_birth).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</dd></div>
            <div><dt>Signature colour</dt><dd className="num">{p.signature_colour}</dd></div>
            <div><dt>Marital status</dt><dd>{label(p.marital_status)}</dd></div>
            <div className="wide"><dt>Home</dt><dd>{address(p)}<br />{p.district}, Birmingham {p.postcode}</dd></div>
            <div className="wide"><dt>Contact</dt><dd>{p.email}<br /><span className="num">{p.phone}</span></dd></div>
            <div><dt>Income</dt><dd className="num">{gbp.format(p.annual_income)}</dd></div>
            <div><dt>Education</dt><dd>{label(p.education_level)}</dd></div>
            {p.workplace && <div className="wide"><dt>Works at</dt><dd>{p.workplace}{p.sector ? ` (${p.sector})` : ''}</dd></div>}
            {p.school && <div className="wide"><dt>Studies at</dt><dd>{p.school}</dd></div>}
            <div><dt>Height</dt><dd className="num">{p.height_cm} cm</dd></div>
            <div><dt>Blood type</dt><dd>{p.blood_type}</dd></div>
            <div><dt>Eyes, hair</dt><dd>{label(p.eye_colour)}, {p.hair_colour}</dd></div>
            <div><dt>Favourite colour</dt><dd>{label(p.favourite_colour)}</dd></div>
          </dl>

          <section>
            <h3>Their day</h3>
            <ol className="day">
              {p.schedule.map((s, i) => {
                const next = p.schedule[i + 1]
                return (
                  <li key={s.seq}>
                    <span className="num t">{fmtTime(s.start_min)}</span>
                    <span><strong>{s.activity_type === 'home' ? 'Home' : s.place_name}</strong>
                      {s.activity_type !== 'home' && <small> {label(s.activity_type)}</small>}
                      {next && <small className="leg">Leaves {fmtTime(s.end_min)} by {p.commute_mode === 'none' ? 'foot' : p.commute_mode}</small>}
                    </span>
                  </li>
                )
              })}
            </ol>
            {km > 0 && <p className="hint">Gold line: road route from pgRouting, {(km / 1000).toFixed(1)} km in total.</p>}
            {p.schedule.length === 1 && <p className="hint">Stays at home all day.</p>}
          </section>

          <section>
            <h3>Lives with</h3>
            {p.household.length === 0 ? <p className="hint">Lives alone ({label(p.household_type)}, {label(p.tenure)}).</p> : (
              <ul className="people">
                {p.household.map((m) => (
                  <li key={m.person_id}>
                    <button onClick={() => onSelect(m.person_id)}>
                      <i style={{ background: m.signature_colour }} />
                      <span>{m.first_name} {m.last_name}</span>
                      <small>{m.relationship ? label(m.relationship) : label(m.household_role)}, {m.age}</small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {p.relatives.length > 0 && (
            <section>
              <h3>Family elsewhere</h3>
              <ul className="people">
                {p.relatives.map((r) => (
                  <li key={r.person_id}>
                    <button onClick={() => onSelect(r.person_id)}>
                      <i style={{ background: r.signature_colour }} />
                      <span>{r.first_name} {r.last_name}</span>
                      <small>{label(r.relationship)}, {r.age}, {r.district}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </aside>
  )
}
