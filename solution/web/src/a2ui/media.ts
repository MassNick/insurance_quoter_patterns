/* Media queries shared by the renderer and the shell (safe where matchMedia does not exist). */
export const matches = (q: string): boolean => typeof matchMedia === 'function' && matchMedia(q).matches;
export const PHONE = '(max-width: 600px)';
export const isPhone = (): boolean => matches(PHONE);
export const reduceMotion = (): boolean => matches('(prefers-reduced-motion: reduce)');
