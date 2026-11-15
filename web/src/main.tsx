import '@fontsource/public-sans/400.css'
import '@fontsource/public-sans/600.css'
import '@fontsource/public-sans/700.css'
import '@fontsource/public-sans/800.css'
import 'maplibre-gl/dist/maplibre-gl.css'
import './styles.css'
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import SimpleApp from './SimpleApp'

const isSimple = () => window.location.hash.startsWith('#/simple')

/** The main app by default; the simplified customer view only at #/simple. */
function Root() {
  const [simple, setSimple] = useState(isSimple)
  useEffect(() => {
    const onHash = () => setSimple(isSimple())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return simple ? <SimpleApp /> : <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
