/** Temporary presenter mode (open the app with ?demo): the live UI, but "Find sites" on a preset plays its recording.
 *  Nothing on screen says the run is recorded. Remove this file and its uses once live runs are demo-ready. */
export const DEMO = typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('demo')
