import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const wrangler = path.join(siteRoot, "node_modules", ".bin", "wrangler");

function dryRun(extraArguments = []) {
  return spawnSync(
    wrangler,
    ["deploy", "--config", "wrangler.jsonc", "--dry-run", ...extraArguments],
    { cwd: siteRoot, encoding: "utf8" },
  );
}

test("production config fails closed unless the assembled asset directory is explicit", () => {
  const result = dryRun();
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout + result.stderr, /assets|entry-point|entry point/i);
});

test("an explicit asset directory remains deployable", () => {
  const result = dryRun(["--assets", "./public"]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
