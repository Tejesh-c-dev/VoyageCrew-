import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

function fixHtml2pdfCanvasInterop() {
  return {
    name: 'fix-html2pdf-canvas-interop',
    transform(code: string, id: string) {
      if (id.replaceAll('\\', '/').endsWith('/html2pdf.js/src/worker.js')) {
        return code.replace(
          "import * as html2canvas from 'html2canvas';",
          "import html2canvas from 'html2canvas';",
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), fixHtml2pdfCanvasInterop()],
  resolve: {
    alias: [{ find: /^html2pdf\.js$/, replacement: 'html2pdf.js/src/index.js' }],
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/jspdf/')) return 'jspdf';
          if (id.includes('/node_modules/html2canvas/')) return 'html2canvas';
        },
      },
    },
  },
});
