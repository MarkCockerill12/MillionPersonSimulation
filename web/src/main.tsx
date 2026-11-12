import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  // No StrictMode: its dev-only double mount would attach deck.gl to MapLibre's WebGL context twice.
  <BrowserRouter>
    <App />
  </BrowserRouter>,
)
