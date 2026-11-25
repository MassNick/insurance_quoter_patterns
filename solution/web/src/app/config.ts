/* Runtime configuration, from Vite env variables only (README, "Configuration"). */
export const BFF_URL = String(import.meta.env.VITE_BFF_URL ?? '').replace(/\/$/, '');
/** The developer trace drawer exists only when the build allows it and the page has ?dev=1 (FR-UI-7). */
export const traceAllowed = (search: string) =>
  import.meta.env.VITE_ENABLE_TRACE !== 'false' && new URLSearchParams(search).get('dev') === '1';
