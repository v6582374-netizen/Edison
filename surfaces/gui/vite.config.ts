import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// `base: "./"` makes built asset URLs relative, so the bundle loads from the `tauri://`
// origin in the desktop shell (absolute `/assets` 404s there); a server-hosted build is
// unaffected. Dev runs on a fixed port (1420) with strictPort so the Tauri webview always
// loads the vite instance Tauri itself spawns (a drifting port would make the window load a
// stale/other server). `tauri.conf.json` devUrl must match this.
export default defineConfig(({ command }) => {
  let devToken = "";
  if (command === "serve") {
    const state =
      process.env.COWORKER_STATE_DIR ||
      (process.platform === "win32"
        ? path.join(process.env.APPDATA || os.homedir(), "coworker")
        : path.join(os.homedir(), ".config", "coworker"));
    try {
      devToken = fs.readFileSync(path.join(state, "sidecar-8765.token"), "utf8").trim();
    } catch {
      // The Tauri dev shell injects its in-memory token at runtime. Plain browser dev
      // shows the normal startup retry until the standalone server/token file exists.
    }
  }
  return {
    base: "./",
    // Run the same pinned source adaptations in dev; esbuild prebundling would
    // otherwise bypass them and pull Foliate's optional PDF dependency back in.
    optimizeDeps: { exclude: ["foliate-js"] },
    plugins: [react(), {
      // Foliate's generic factory imports PDF even when open() receives our EPUB
      // object. Keep that optional adapter outside this EPUB-only integration.
      name: "reader-epub-only",
      enforce: "pre",
      resolveId(source, importer) {
        if (source === "./pdf.js" && importer?.replaceAll("\\", "/").endsWith("/foliate-js/view.js")) {
          return path.resolve(__dirname, "src/components/reader/unsupported-pdf.js");
        }
      },
      transform(code, id) {
        if (id.replaceAll("\\", "/").endsWith("/foliate-js/search.js")) {
          // A match ending exactly at a text-node boundary belongs to that
          // node's end, not the following node (which may not exist).
          const before = "while (sum <= end) sum += strs[++strIndex].length";
          if (code.split(before).length !== 2) throw new Error("Review Foliate search endpoint patch for the new upstream version");
          return {code: code.replace(before, "while (sum < end) sum += strs[++strIndex].length"), map: null};
        }
        if (!id.replaceAll("\\", "/").endsWith("/foliate-js/paginator.js")) return;
        // Pinned upstream assumes an iframe still has a document when queued
        // resize/font callbacks run. Firefox/WebKit disproved that on unmount.
        const patches = [
          ["const sel = doc.getSelection()\n                if (!sel.rangeCount) return", "const sel = doc.getSelection()\n                if (!sel?.rangeCount) return"],
          ["    expand() {\n        const { documentElement } = this.document", "    expand() {\n        if (!this.document) return\n        const { documentElement } = this.document"],
          ["requestAnimationFrame(() =>\n            this.#background.style.background = getBackground(this.#view.document))", "requestAnimationFrame(() =>\n            this.#view?.document && (this.#background.style.background = getBackground(this.#view.document)))"],
          ["fonts?.ready?.then(() => this.#view.expand())", "fonts?.ready?.then(() => this.#view?.expand())"],
          ["this.#view.destroy()\n        this.#view = null", "this.#view?.destroy()\n        this.#view = null"],
        ];
        for (const [before, after] of patches) {
          if (code.split(before).length !== 2) throw new Error("Review Foliate lifecycle patch for the new upstream version");
          code = code.replace(before, after);
        }
        return {code, map: null};
      },
    }],
    server: { port: 1420, strictPort: true },
    define: { __COWORKER_DEV_TOKEN__: JSON.stringify(devToken) },
    // Tauri CLI looks for these; harmless for the browser build.
    clearScreen: false,
    envPrefix: ["VITE_", "TAURI_"],
  };
});
