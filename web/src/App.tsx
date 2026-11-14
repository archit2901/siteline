// Two views over one run: the customer view (default) and the developer view (#/dev).
import { useEffect, useState } from 'react'
import { useSiteline } from './state/useSiteline'
import CustomerView from './views/CustomerView'
import DeveloperView from './views/DeveloperView'

const isDev = () => window.location.hash.startsWith('#/dev')

export default function App() {
  const s = useSiteline()
  const [dev, setDev] = useState(isDev)
  useEffect(() => {
    const onHash = () => setDev(isDev())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return dev ? <DeveloperView s={s} /> : <CustomerView s={s} />
}
