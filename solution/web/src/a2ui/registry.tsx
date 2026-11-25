/* Component registry: A2UI component name -> React component. Basic catalog components render on any
   surface; insurance components only on surfaces created with the insurance catalog. Anything else
   renders the safe Unknown fallback, never the component's own content. */
import type { ComponentType } from 'react';
import { useSurface } from './binding';
import { AssignmentBoard } from './components/AssignmentBoard';
import {
  CheckBox,
  ChoicePicker,
  Column,
  DateTimeInput,
  InPlaceNote,
  type Props,
  Row,
  Slider,
  Summary,
  Text,
  TextField,
  Unknown,
} from './components/basic';
import { DocumentDrop, FileDrop } from './components/documents';
import { AssignmentMatrix, OfferCards, Repeater } from './components/lists';
import { AddressLookup, VinField, YmmPicker } from './components/vehicle';
import { isVisible } from './tree';
import { type A2Component, CATALOG_ID } from './types';

const basic = (render: ComponentType<Props>) => ({ render, insurance: false });
const insurance = (render: ComponentType<Props>) => ({ render, insurance: true });

export const REGISTRY: Record<string, { render: ComponentType<Props>; insurance: boolean }> = {
  Column: basic(Column),
  Row: basic(Row),
  Text: basic(Text),
  TextField: basic(TextField),
  DateTimeInput: basic(DateTimeInput),
  ChoicePicker: basic(ChoicePicker),
  CheckBox: basic(CheckBox),
  Slider: basic(Slider),
  InPlaceNote: insurance(InPlaceNote),
  Summary: insurance(Summary),
  YmmPicker: insurance(YmmPicker),
  VinField: insurance(VinField),
  AddressLookup: insurance(AddressLookup),
  Repeater: insurance(Repeater),
  AssignmentMatrix: insurance(AssignmentMatrix),
  AssignmentBoard: insurance(AssignmentBoard),
  OfferCards: insurance(OfferCards),
  DocumentDrop: insurance(DocumentDrop),
  FileDrop: insurance(FileDrop),
};

export function RenderComponent({ c, scope }: { c: A2Component; scope?: string }) {
  const { surface } = useSurface();
  if (!isVisible(c, surface, scope)) return null;
  const entry = REGISTRY[c.component];
  if (!entry || (entry.insurance && surface.catalogId !== CATALOG_ID)) return <Unknown c={c} />;
  const C = entry.render;
  return <C c={c} scope={scope} />;
}

export function Node({ id, scope }: { id: string; scope?: string }) {
  const c = useSurface().surface.components[id];
  return c ? <RenderComponent c={c} scope={scope} /> : null;
}
