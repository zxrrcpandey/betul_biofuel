import { fileURLToPath, URL } from "node:url"

import vue from "@vitejs/plugin-vue"
import { defineConfig } from "vite"

// Built assets are committed under trustbit_ethanol/public/exec/ and served
// from /assets/trustbit_ethanol/exec/ (bench symlinks {app}/public into
// sites/assets/{app}). The shell HTML is relocated to www/exec.html by
// scripts/postbuild.mjs, which also emits the Jinja/csrf markers.
export default defineConfig({
  plugins: [vue()],
  base: "/assets/trustbit_ethanol/exec/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "../trustbit_ethanol/public/exec",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./index.html", import.meta.url)),
    },
  },
  server: {
    port: 8080,
    proxy: {
      // Dev convenience only — production serves same-origin from the bench.
      "^/(api|assets|files)/.*": {
        target: "https://betulbiofuel.mkasystem.com",
        changeOrigin: true,
        secure: true,
      },
    },
  },
})
