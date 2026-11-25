import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import { clock } from '../../src/a2ui/catalog';

clock.now = () => new Date('2026-11-25T12:00:00');
afterEach(cleanup);
