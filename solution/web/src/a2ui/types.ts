/* A2UI 1.0 messages and the insurance catalog component shapes this renderer understands.
   Source of truth: architecture.md § 7, contracts/events.md (a2ui.message), and the compiler output in
   fixtures/surfaces.json (prototype compiler, which mcp-a2ui must match byte for byte). */

export const A2UI_VERSION = '1.0';
export const CATALOG_ID = 'https://catalog.example.com/insurance-catalog/v1/catalog.json';
export const BASIC_CATALOG_ID = 'https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json';

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type DataModel = { [k: string]: Json };

export interface FunctionCall {
  '@call': string;
  args: Record<string, unknown>;
}
export interface CheckRule {
  condition: FunctionCall;
  message: string;
}

/** Every component is an object with an id and a type name; the rest depends on the type. */
export interface A2Component {
  id: string;
  component: string;
  // biome-ignore lint/suspicious/noExplicitAny: component props are open-ended per the catalog
  [prop: string]: any;
}

export interface CreateSurface {
  surfaceId: string;
  catalogId?: string;
  dataModel?: DataModel;
  components?: A2Component[];
}
export interface A2uiMessage {
  version: string;
  createSurface?: CreateSurface;
  updateComponents?: { surfaceId: string; components: A2Component[] };
  updateDataModel?: { surfaceId: string; path?: string; value: Json | undefined };
  deleteSurface?: { surfaceId: string };
  agentFunctionResponse?: { functionCallId: string; value: unknown };
}

export interface Provenance {
  source: string;
  note?: string;
}

/** The A2UI 1.0 action message the renderer sends when a Button fires. */
export interface A2uiAction {
  name: string;
  surfaceId: string;
  sourceComponentId: string;
  timestamp: string;
  context: Record<string, unknown>;
}

/** Callbacks a surface needs from its host (the app, a test, or the visual harness). */
export interface SurfaceHost {
  onAction(action: A2uiAction): void;
  callFunction(surfaceId: string, call: string, args: Record<string, unknown>): Promise<unknown>;
  uploadDocument(surfaceId: string, file: File, purpose: string, fieldPath?: string): Promise<{ document_id: string }>;
}

export interface DocumentField {
  value: string | number | boolean;
  confidence: number;
}
export interface DocumentExtraction {
  document_ok: boolean;
  reason?: string;
  fields?: Record<string, DocumentField>;
}
