import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// The planning UI is a single-page React app. Vite builds one IIFE
// bundle (no module loader needed) plus a CSS file, both dropped into
// `../static/build/` so Flask's static handler serves them directly.
//
// `process.env.NODE_ENV` is replaced at build time so dev-only branches
// in deps (notably react-arborist) tree-shake out.
export default defineConfig({
  plugins: [react()],
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    'process.env': '{}',
  },
  build: {
    outDir: resolve(__dirname, '../static/build'),
    emptyOutDir: true,
    sourcemap: true,
    lib: {
      entry: resolve(__dirname, 'src/main.jsx'),
      name: 'KatoPlanningUI',
      formats: ['iife'],
      fileName: () => 'app.js',
    },
    rollupOptions: {
      output: {
        assetFileNames: 'app[extname]',
      },
    },
  },
});
