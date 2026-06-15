#!/usr/bin/env node
/**
 * Tiny test runner for the Playwright e2e specs in this directory.
 *
 * Each .spec.js exports a default async function returning the literal
 * string 'pass' (or anything else / throws on failure). The harness
 * runs them sequentially against a running server at MM_BASE_URL (or
 * http://localhost:3000 by default) and prints a tally.
 *
 * Exits 0 on all-pass, 1 on any fail, 2 if Playwright isn't installed.
 *
 * Why not @playwright/test: that ships its own test runner that wants
 * a config file + workers + a project hierarchy. For our 4 specs the
 * cost of that ceremony beats the convenience. The playwright API
 * itself (chromium.launch + page.locator + page.dragTo) is the same.
 */
import { readdirSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');

if (!existsSync(path.join(repoRoot, 'node_modules', 'playwright'))) {
  console.error('Playwright not installed. Run: npm install');
  process.exit(2);
}

const filter = process.argv[2] || '';
const specs = readdirSync(here)
  .filter(f => f.endsWith('.spec.js'))
  .filter(f => !filter || f.includes(filter))
  .sort();

if (specs.length === 0) {
  console.error('No specs matched filter:', filter);
  process.exit(2);
}

let pass = 0, fail = 0;
const failed = [];
for (const spec of specs) {
  process.stdout.write(`▶ ${spec} ... `);
  const url = pathToFileURL(path.join(here, spec)).href;
  try {
    const mod = await import(url);
    const result = await mod.default();
    if (result === 'pass') { console.log('PASS'); pass++; }
    else { console.log('FAIL', result); fail++; failed.push(spec); }
  } catch (e) {
    console.log('FAIL\n   ', e.message);
    fail++; failed.push(spec);
  }
}
console.log(`\nTotal: ${pass} pass, ${fail} fail` + (failed.length ? ` (${failed.join(', ')})` : ''));
process.exit(fail === 0 ? 0 : 1);
