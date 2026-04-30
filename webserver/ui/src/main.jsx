// Single entry point for the planning UI. Mounts <App /> at #root.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';

function bootstrap() {
  const mountPoint = document.getElementById('root');
  if (!mountPoint) {
    return;
  }
  createRoot(mountPoint).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
} else {
  bootstrap();
}
