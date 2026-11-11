import { lazy, Suspense } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import MapPage from './pages/MapPage'

const Analytics = lazy(() => import('./pages/Analytics'))
const SqlConsole = lazy(() => import('./pages/SqlConsole'))

export default function App() {
  return (
    <>
      <nav className="nav" aria-label="Sections">
        <NavLink to="/" end>Map</NavLink>
        <NavLink to="/analytics">Analytics</NavLink>
        <NavLink to="/sql">SQL console</NavLink>
      </nav>
      <Suspense fallback={<p className="status page">Loading…</p>}>
        <Routes>
          <Route path="/" element={<MapPage />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/sql" element={<SqlConsole />} />
        </Routes>
      </Suspense>
    </>
  )
}
