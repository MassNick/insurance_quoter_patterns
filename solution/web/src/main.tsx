import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/prototype.css';
import './styles/shell.css';
import './styles/additions.css';
import { httpApi } from './app/api';
import { App } from './app/components/App';
import { BFF_URL, traceAllowed } from './app/config';
import { AppContext } from './app/context';
import { Session } from './app/session';
import { createStore } from './app/store';

const session = new Session(createStore(), httpApi(BFF_URL));
session.start();

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <AppContext.Provider value={session}>
      <App dev={traceAllowed(location.search)} />
    </AppContext.Provider>
  </StrictMode>,
);
