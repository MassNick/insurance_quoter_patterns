/* React access to the store and the session controller. */
import { createContext, useContext, useSyncExternalStore } from 'react';
import type { Session } from './session';
import type { AppState } from './store';

export const AppContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(AppContext);
  if (!s) throw new Error('AppContext missing');
  return s;
}

/** Subscribe to a slice of the app state. Selectors must return stored references, not new objects. */
export function useApp<T>(select: (s: AppState) => T): T {
  const { store } = useSession();
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}
