// Production front-end build: compiles Tailwind and copies the libraries in assets.json into ../static.
// Run from frontend/: `npm ci && npm run build` (the Docker image does this in its "assets" stage).
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "static");
const { libraries } = JSON.parse(readFileSync(join(here, "assets.json"), "utf8"));

mkdirSync(join(staticDir, "vendor"), { recursive: true });
for (const [name, lib] of Object.entries(libraries)) {
  const pkg = lib.package.slice(0, lib.package.lastIndexOf("@"));
  const version = lib.package.slice(lib.package.lastIndexOf("@") + 1);
  const installed = JSON.parse(readFileSync(join(here, "node_modules", pkg, "package.json"), "utf8")).version;
  if (installed !== version) throw new Error(`${name}: assets.json wants ${pkg}@${version} but ${installed} is installed`);
  const target = join(staticDir, "vendor", lib.vendor);
  // Drop source-map comments: the .map files aren't shipped, and the static-files manifest would reject the dangling refs.
  const text = readFileSync(join(here, "node_modules", pkg, lib.file), "utf8")
    .replace(/^\/\/# sourceMappingURL=.*$/gm, "").replace(/\/\*# sourceMappingURL=[^*]*\*\//g, "");
  writeFileSync(target, text);
  console.log(`vendor/${lib.vendor}  (${pkg}@${version}, ${Math.round(statSync(target).size / 1024)} KB)`);
}

// tailwind.css lives here, beside node_modules, so `@import "tailwindcss"` resolves; its @source paths point at
// the templates, apps and static/js the class names come from.
const input = join(here, "tailwind.css");
const cli = join(here, "node_modules", "@tailwindcss", "cli", "dist", "index.mjs");
execFileSync(process.execPath, [cli, "-i", input, "-o", join(staticDir, "css", "app.css"), "--minify"], { stdio: "inherit", cwd: here });
console.log(`css/app.css  (${Math.round(statSync(join(staticDir, "css", "app.css")).size / 1024)} KB)`);
