/* The A2UI message reducer. Pure: (surfaces, action) -> surfaces.
   Server messages: createSurface, updateComponents, updateDataModel (null deletes; /_provenance badges;
   /_errors server-side errors), deleteSurface, agentFunctionResponse. Local actions come from the
   renderer (typing in a field, a lookup answer, a failed submit). */
import { escapeKey, get, provPath, remove, set } from './pointer';
import { type A2Component, A2UI_VERSION, type A2uiMessage, BASIC_CATALOG_ID, CATALOG_ID, type DataModel, type Json } from './types';

export const SUPPORTED_CATALOGS = [CATALOG_ID, BASIC_CATALOG_ID];

export interface SurfaceState {
  id: string;
  catalogId: string;
  components: Record<string, A2Component>;
  dataModel: DataModel;
  deleted: boolean;
  /** Paths the customer changed; a live autofill on them becomes a suggestion instead (FR-A2UI-7). */
  dirty: Record<string, true>;
  suggestions: Record<string, Json>;
  /** path -> counter, bumped by every server value so the field plays the autofill flash. */
  flash: Record<string, number>;
  /** Errors shown after a failed submit or a blur, keyed by bound path. */
  shown: Record<string, string>;
  errorCount: number;
  repeaterCounts: Record<string, number>;
  lookups: Record<string, boolean>;
}

export interface SurfacesState {
  byId: Record<string, SurfaceState>;
  functionResponses: Record<string, unknown>;
  rejected: string[];
}

export const emptySurfaces = (): SurfacesState => ({ byId: {}, functionResponses: {}, rejected: [] });

export type LocalAction =
  | { type: 'set'; surfaceId: string; path: string; value: unknown; silent?: boolean }
  | { type: 'provenance'; surfaceId: string; path: string; value: { source: string; note?: string } }
  | { type: 'acceptSuggestion'; surfaceId: string; path: string }
  | { type: 'showErrors'; surfaceId: string; errors: Record<string, string>; merge?: boolean }
  | { type: 'repeaterCount'; surfaceId: string; componentId: string; count: number; removeIndexPath?: string }
  | { type: 'lookup'; surfaceId: string; key: string; valid: boolean }
  | { type: 'purge'; surfaceId: string };

export type SurfaceAction = { type: 'message'; message: A2uiMessage } | LocalAction;

function fresh(id: string, catalogId: string, components: A2Component[], dataModel: DataModel): SurfaceState {
  return {
    id,
    catalogId,
    components: Object.fromEntries(components.map((c) => [c.id, c])),
    dataModel,
    deleted: false,
    dirty: {},
    suggestions: {},
    flash: {},
    shown: {},
    errorCount: 0,
    repeaterCounts: {},
    lookups: {},
  };
}

const isMeta = (path: string) => path.startsWith('/_');
const clone = <T>(v: T): T => (v === undefined ? v : structuredClone(v));

function applyDataModel(s: SurfaceState, path: string | undefined, value: Json | undefined): SurfaceState {
  if (!path || path === '/') return { ...s, dataModel: (value ?? {}) as DataModel, dirty: {}, suggestions: {} };
  if (value === null) return { ...s, dataModel: remove(s.dataModel, path) };
  if (!isMeta(path) && s.dirty[path] && JSON.stringify(value) !== JSON.stringify(valueAt(s, path))) {
    return { ...s, suggestions: { ...s.suggestions, [path]: clone(value) as Json } };
  }
  const next = { ...s, dataModel: set(s.dataModel, path, clone(value)) };
  if (isMeta(path)) return next;
  return { ...next, flash: { ...s.flash, [path]: (s.flash[path] ?? 0) + 1 } };
}

const valueAt = (s: SurfaceState, path: string): unknown => get(s.dataModel, path);

function withSurface(state: SurfacesState, id: string, fn: (s: SurfaceState) => SurfaceState): SurfacesState {
  const s = state.byId[id];
  return s ? { ...state, byId: { ...state.byId, [id]: fn(s) } } : state;
}

export function applyMessage(state: SurfacesState, msg: A2uiMessage): SurfacesState {
  if (msg?.version !== A2UI_VERSION) return { ...state, rejected: [...state.rejected, `version ${String(msg?.version)} not supported`] };
  let next = state;
  if (msg.createSurface) {
    const cs = msg.createSurface;
    const catalogId = cs.catalogId ?? BASIC_CATALOG_ID;
    if (!SUPPORTED_CATALOGS.includes(catalogId)) return { ...state, rejected: [...state.rejected, `catalog ${catalogId} not supported`] };
    next = {
      ...next,
      byId: { ...next.byId, [cs.surfaceId]: fresh(cs.surfaceId, catalogId, cs.components ?? [], clone(cs.dataModel ?? {})) },
    };
  }
  if (msg.updateComponents) {
    const u = msg.updateComponents;
    next = withSurface(next, u.surfaceId, (s) => ({
      ...s,
      components: { ...s.components, ...Object.fromEntries(u.components.map((c) => [c.id, c])) },
    }));
  }
  if (msg.updateDataModel) {
    const u = msg.updateDataModel;
    next = withSurface(next, u.surfaceId, (s) => applyDataModel(s, u.path, u.value));
  }
  if (msg.deleteSurface) next = withSurface(next, msg.deleteSurface.surfaceId, (s) => ({ ...s, deleted: true }));
  if (msg.agentFunctionResponse) {
    const r = msg.agentFunctionResponse;
    next = { ...next, functionResponses: { ...next.functionResponses, [r.functionCallId]: r.value } };
  }
  return next;
}

function clearError(s: SurfaceState, path: string): SurfaceState {
  const errs = (s.dataModel._errors ?? {}) as Record<string, Json>;
  const dataModel = path in errs ? remove(s.dataModel, `/_errors/${escapeKey(path)}`) : s.dataModel;
  if (!(path in s.shown) && dataModel === s.dataModel) return s;
  const { [path]: _gone, ...shown } = s.shown;
  return { ...s, shown, dataModel };
}

export function surfacesReducer(state: SurfacesState, action: SurfaceAction): SurfacesState {
  switch (action.type) {
    case 'message':
      return applyMessage(state, action.message);
    case 'set':
      return withSurface(state, action.surfaceId, (s) => {
        const { [action.path]: _old, ...suggestions } = s.suggestions;
        const next = { ...s, dataModel: set(s.dataModel, action.path, action.value), suggestions };
        return action.silent ? next : clearError({ ...next, dirty: { ...s.dirty, [action.path]: true } }, action.path);
      });
    case 'provenance':
      return withSurface(state, action.surfaceId, (s) => ({ ...s, dataModel: set(s.dataModel, provPath(action.path), action.value) }));
    case 'acceptSuggestion':
      return withSurface(state, action.surfaceId, (s) => {
        const { [action.path]: v, ...suggestions } = s.suggestions;
        return {
          ...s,
          suggestions,
          dataModel: set(s.dataModel, action.path, v),
          flash: { ...s.flash, [action.path]: (s.flash[action.path] ?? 0) + 1 },
        };
      });
    case 'showErrors':
      return withSurface(state, action.surfaceId, (s) => {
        const shown = action.merge ? { ...s.shown, ...action.errors } : action.errors;
        for (const [k, v] of Object.entries(shown)) if (!v) delete shown[k];
        return { ...s, shown, errorCount: action.merge ? s.errorCount : Object.keys(shown).length };
      });
    case 'repeaterCount':
      return withSurface(state, action.surfaceId, (s) => {
        let dataModel = s.dataModel;
        if (action.removeIndexPath) {
          const i = Number(action.removeIndexPath.split('/').pop());
          const coll = action.removeIndexPath.slice(0, action.removeIndexPath.lastIndexOf('/'));
          const arr = valueAt(s, coll);
          if (Array.isArray(arr))
            dataModel = set(
              dataModel,
              coll,
              arr.filter((_, j) => j !== i),
            );
        }
        return { ...s, dataModel, repeaterCounts: { ...s.repeaterCounts, [action.componentId]: action.count } };
      });
    case 'lookup':
      return withSurface(state, action.surfaceId, (s) => ({ ...s, lookups: { ...s.lookups, [action.key]: action.valid } }));
    case 'purge': {
      const { [action.surfaceId]: _gone, ...byId } = state.byId;
      return { ...state, byId };
    }
  }
}
