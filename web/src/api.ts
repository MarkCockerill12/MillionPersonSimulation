export interface Member {
  person_id: number
  first_name: string
  last_name: string
  signature_colour: string
  age: number
  household_role?: string
  relationship?: string | null
}

export interface Relative extends Member {
  street: string
  house_number: number
  postcode: string
  district: string
  lon: number
  lat: number
}

export interface Stop {
  seq: number
  activity_type: string
  start_min: number
  end_min: number
  place_id: number | null
  place_name: string
  category: string | null
  lon: number
  lat: number
}

export interface Person {
  person_id: number
  citizen_id: string
  first_name: string
  middle_name: string | null
  last_name: string
  sex: 'F' | 'M'
  date_of_birth: string
  age: number
  marital_status: string
  email: string
  phone: string
  signature_colour: string
  favourite_colour: string
  eye_colour: string
  hair_colour: string
  height_cm: number
  blood_type: string
  education_level: string
  employment_status: string
  annual_income: number
  commute_mode: string
  household_role: string
  occupation: string | null
  sector: string | null
  workplace_id: number | null
  workplace: string | null
  school_id: number | null
  school: string | null
  household_id: number
  household_type: string
  tenure: string
  flat_number: number | null
  house_number: number
  street: string
  postcode: string
  district: string
  lon: number
  lat: number
  household: Member[]
  relatives: Relative[]
  schedule: Stop[]
}

export interface Analysis {
  id: string
  title: string
  description: string
  chart: string
  x?: string
  y?: string
  sql?: string
  columns?: string[]
  rows?: (string | number | null)[][]
  elapsed_ms?: number
}

export interface Meta {
  people: number
  households: number
  places: number
  districts: number
  relationships: number
  activities: number
  bbox: [number, number, number, number]
  db_size: string
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  meta: () => get<Meta>('/api/meta'),
  person: (id: number) => get<Person>(`/api/people/${id}`),
  route: (id: number) => get<GeoJSON.FeatureCollection>(`/api/people/${id}/route`),
  search: (q: string) => get<Member[]>(`/api/search?q=${encodeURIComponent(q)}`),
  analyses: () => get<Analysis[]>('/api/analytics'),
  analysis: (id: string) => get<Analysis>(`/api/analytics/${id}`),
  schema: () => get<Record<string, { name: string; type: string }[]>>('/api/sql/schema'),
  sql: async (sql: string) => {
    const r = await fetch('/api/sql', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sql }) })
    const body = await r.json()
    if (!r.ok) throw new Error(body.detail ?? 'The query failed.')
    return body as { columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean; elapsed_ms: number }
  },
}

const ACRONYMS: Record<string, string> = { gcse: 'GCSE', a_level: 'A level' }
export const label = (s: string | null | undefined) =>
  ACRONYMS[s ?? ''] ?? (s ?? '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
