// Live event stream: one RunEvent JSON per SSE message. The server sends the full history to every new
// subscriber, so after EventSource's automatic reconnect we skip events whose seq we've already seen.
import type { RunEvent } from '../types/contracts'

export interface Subscription {
  close: () => void
}

export function subscribeToRun(
  runId: string,
  baseUrl: string,
  onEvent: (event: RunEvent) => void,
  onError: (message: string) => void,
): Subscription {
  let lastSeq = -1
  let finished = false
  const source = new EventSource(`${baseUrl}/runs/${runId}/events`)
  source.onmessage = (msg) => {
    const event = JSON.parse(msg.data as string) as RunEvent
    if (event.seq <= lastSeq) return
    lastSeq = event.seq
    onEvent(event)
    if (event.type === 'done' || (event.type === 'error' && !event.payload?.recoverable)) {
      finished = true
      source.close()
    }
  }
  source.onerror = () => {
    if (finished) return
    if (source.readyState === EventSource.CLOSED) onError('Lost the connection to the agent. Start a new search.')
  }
  return { close: () => source.close() }
}
