import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    outDir: path.resolve(__dirname, "../frontend"),
    emptyOutDir: true,
  },
});
