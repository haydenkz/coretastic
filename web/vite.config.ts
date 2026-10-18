import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // Relative asset URLs, so the flasher works from any path, such as the
  // GitHub Pages project site at /coretastic/.
  base: "./",
  plugins: [tailwindcss()],
});
