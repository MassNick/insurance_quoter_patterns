/* The brand mark, in one replaceable module.
   TODO(NFR-BRD-1): this is the prototype's hand-drawn placeholder approximating the Desjardins hexagon.
   Replace it with the official Desjardins asset from the brand team before any external showing; the
   brand team approves the build. Nothing else in the app draws the logo. */

export function BrandMark() {
  return (
    <svg viewBox="0 0 40 44" aria-hidden="true" data-placeholder-brand="true">
      <path d="M20 3.5 35.5 12.5v19L20 40.5 4.5 31.5v-19z" fill="none" stroke="currentColor" strokeWidth="5" strokeLinejoin="round" />
      <path d="M20 14.5 27 18.5v7L20 29.5 13 25.5v-7z" fill="currentColor" />
    </svg>
  );
}

export const BRAND_NAME = 'Desjardins';
export const BRAND_SUB = 'Assurances';
