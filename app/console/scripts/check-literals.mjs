#!/usr/bin/env node
// Market literal gate (spec §2.5, acceptance #9): fail if any console source file
// hard-codes a currency symbol, a distance/efficiency unit, or Indian-numbering
// words in rendered text. Units and currency must come from lib/market.ts.
// Zero dependencies so it runs in CI before `next build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("../src", import.meta.url).pathname;
const ALLOW = new Set(["lib/market.ts"]); // the one place units are spelled out

// Patterns that indicate a literal unit or currency in UI code.
const RULES = [
  { re: /₹|\blakh\b|\bcrore\b/i, why: "Indian currency/numbering literal" },
  { re: /[€£]/, why: "currency symbol literal" },
  { re: /(["'`>]|\$\{[^}]*\}\s*)\$\s?\d/, why: "hard-coded $ amount" },
  { re: /\$\{[^}]*\}\s?(mi|km|mpg|mpge|kWh\/100\s?km|l\/100\s?km)\b/, why: "unit appended to a value" },
  { re: /\d\s?(mi|km|mpg|mpge)\b["'`<]/, why: "number with a literal unit" },
  { re: /["'`]\s?(mi|km|mpg|MPG|mpge|MPGe|l\/100km|kWh\/100km)\s?["'`]/, why: "unit string literal" },
  { re: /toLocaleString\(\s*["'](en-US|de-DE|en-IN)["']/, why: "hard-coded locale" },
];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(tsx?|jsx?)$/.test(name)) yield p;
  }
}

const problems = [];
for (const file of walk(ROOT)) {
  const rel = relative(ROOT, file);
  if (ALLOW.has(rel)) continue;
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return; // comments are documentation
    for (const { re, why } of RULES) {
      if (re.test(line)) problems.push(`src/${rel}:${i + 1}: ${why}: ${line.trim()}`);
    }
  });
}

if (problems.length) {
  console.error(`check-literals: ${problems.length} market literal(s) — format via lib/market.ts\n`);
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("check-literals: OK (no market literals in src/)");
