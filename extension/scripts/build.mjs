import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const rootDir = resolve(__dirname, "..");
const contractsDir = resolve(rootDir, "../packages/contracts");

async function main() {
  // 1. 构建 Sidepanel React 应用（并复制 public/manifest.json 与 icons/）
  await build({
    root: rootDir,
    base: "./",
    plugins: [react()],
    resolve: {
      alias: { "@contracts": contractsDir },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      rollupOptions: {
        input: {
          sidepanel: resolve(rootDir, "sidepanel.html"),
        },
      },
    },
  });

  // 2. 构建独立单文件 MV3 Service Worker (background.js)
  await build({
    root: rootDir,
    configFile: false,
    publicDir: false,
    resolve: {
      alias: { "@contracts": contractsDir },
    },
    build: {
      outDir: "dist",
      emptyOutDir: false,
      lib: {
        entry: resolve(rootDir, "src/background/index.ts"),
        name: "PoE2WorkbenchBackground",
        formats: ["iife"],
        fileName: () => "background.js",
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
        },
      },
    },
  });

  // 3. 构建独立单文件 Content Script (content.js, IIFE 无外部 import)
  await build({
    root: rootDir,
    configFile: false,
    publicDir: false,
    resolve: {
      alias: { "@contracts": contractsDir },
    },
    build: {
      outDir: "dist",
      emptyOutDir: false,
      lib: {
        entry: resolve(rootDir, "src/content/poeNinja.ts"),
        name: "PoE2WorkbenchContent",
        formats: ["iife"],
        fileName: () => "content.js",
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
        },
      },
    },
  });

  console.log("✔ PoE2 Workbench extension built to extension/dist/");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
