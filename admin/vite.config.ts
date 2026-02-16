import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  base: "/admin/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@review": path.resolve(__dirname, "./src-review"),
    },
  },
  build: {
    outDir: "../app/static/admin",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        admin: path.resolve(__dirname, "index.html"),
        review: path.resolve(__dirname, "review.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
})
