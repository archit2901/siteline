// The simplified customer view, opt-in at #/simple. The main app (App.tsx) stays the default at /.
// Its "developer view" link (#/dev) leads back to the main app.
import { useSiteline } from './state/useSiteline'
import CustomerView from './views/CustomerView'
import './views/customer.css'

export default function SimpleApp() {
  const s = useSiteline()
  return <CustomerView s={s} />
}
