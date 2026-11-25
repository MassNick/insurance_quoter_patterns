/* Visual regression harness (visual.html?el=<name>): renders exactly one element from fixtures, then
   sets body[data-ready] for the screenshot. Dev and test only; not part of the production build. */
import { createRoot } from 'react-dom/client';
import '../styles/prototype.css';
import '../styles/shell.css';
import '../styles/additions.css';
import { clock } from '../a2ui/catalog';
import { ELEMENTS } from './list';
import { SCENES } from './scenes';

clock.now = () => new Date('2026-11-25T12:00:00');
const name = new URLSearchParams(location.search).get('el') ?? '';
const scene = SCENES[name];
const host = document.getElementById('scene') as HTMLElement;

if (!scene)
  createRoot(host).render(
    <ul style={{ padding: 24 }}>
      {ELEMENTS.map((e) => (
        <li key={e.name}>
          <a href={`?el=${e.name}`}>{e.name}</a>
        </li>
      ))}
    </ul>,
  );
else {
  createRoot(host).render(scene.render());
  setTimeout(async () => {
    await scene.play?.(host);
    await new Promise((r) => setTimeout(r, 650)); // past the conversation's 520 ms settle scroll
    document.body.dataset.ready = '1';
  }, 60);
}
