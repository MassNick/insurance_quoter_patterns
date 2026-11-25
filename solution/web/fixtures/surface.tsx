/* A fixture surface mounted with its own reducer and a fake host: the one test host shared by the
   component tests and the visual harness. */
import { useReducer } from 'react';
import { SurfaceContext } from '../src/a2ui/binding';
import { applyMessage, emptySurfaces, type SurfaceAction, type SurfaceState, surfacesReducer } from '../src/a2ui/reducer';
import { Node } from '../src/a2ui/registry';
import { FormCard } from '../src/a2ui/Surface';
import { validate } from '../src/a2ui/tree';
import type { A2uiAction, A2uiMessage, SurfaceHost } from '../src/a2ui/types';
import { callFunction } from './services';
import { createSurface, sampleOf } from './surfaces';

export interface FakeHost extends SurfaceHost {
  actions: A2uiAction[];
  calls: [string, Record<string, unknown>][];
  uploads: [string, string][];
}

/** Functions answered from fixtures/services; uploads succeed, or never finish with `pendingUploads`. */
export function fakeHost(pendingUploads = false): FakeHost {
  const h: FakeHost = {
    actions: [],
    calls: [],
    uploads: [],
    onAction: (a) => void h.actions.push(a),
    callFunction: async (_sid, call, args) => {
      h.calls.push([call, args]);
      return callFunction(call, args);
    },
    uploadDocument: (_sid, file, purpose) => {
      h.uploads.push([file.name, purpose]);
      return pendingUploads ? new Promise(() => undefined) : Promise.resolve({ document_id: `doc_${h.uploads.length}` });
    },
  };
  return h;
}

export interface FixtureProps {
  id: string;
  host: SurfaceHost;
  /** Server values (updateDataModel) applied on top of the fixture. */
  data?: Record<string, unknown>;
  sample?: boolean;
  /** Customer edits applied before `extra` (they make fields dirty). */
  local?: SurfaceAction[];
  extra?: A2uiMessage[];
  errors?: boolean;
  /** Render one component of the surface instead of the whole card. */
  component?: string;
  onState?: (s: SurfaceState) => void;
}

export function FixtureSurface({ id, host, data, sample, local, extra, errors, component, onState }: FixtureProps) {
  const [state, dispatch] = useReducer(surfacesReducer, null, () => {
    let s = applyMessage(emptySurfaces(), createSurface(id));
    for (const [path, value] of Object.entries({ ...(sample ? sampleOf(id) : {}), ...data }))
      s = applyMessage(s, { version: '1.0', updateDataModel: { surfaceId: id, path, value: value as never } });
    for (const a of local ?? []) s = surfacesReducer(s, a);
    for (const m of extra ?? []) s = applyMessage(s, m);
    if (errors) s = surfacesReducer(s, { type: 'showErrors', surfaceId: id, errors: validate(s.byId[id]) });
    return { ...s, byId: { ...s.byId, [id]: { ...s.byId[id], flash: {} } } };
  });
  const surface = state.byId[id];
  onState?.(surface);
  const send = (a: SurfaceAction) => dispatch(a);
  if (!component) return <FormCard surface={surface} dispatch={send} host={host} />;
  return (
    <SurfaceContext.Provider value={{ surface, dispatch: send, host }}>
      <div className="vis-frame form-body">
        <div className="a2-col">
          <Node id={component} />
        </div>
      </div>
    </SurfaceContext.Provider>
  );
}
