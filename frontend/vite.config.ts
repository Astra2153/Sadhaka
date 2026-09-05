import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { port: 5173 },
  build: {
    // Recharts and the bundled run data are both large and neither is needed
    // to render the first paint of most pages, so they are split out. Without
    // this the whole app ships as one 800kB chunk and the overview page waits
    // on chart code it does not use.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
});
