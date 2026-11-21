import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const BIN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "undokey.js");

export function tmpdir(prefix = "undokey-test-") {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

export function sh(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { encoding: "utf8", ...opts });
  if (r.error) throw r.error;
  return r;
}

/** A real git repo with one commit. */
export function makeRepo(files = { "a.txt": "hi\n" }) {
  const dir = path.join(tmpdir(), "proj");
  fs.mkdirSync(dir, { recursive: true });
  writeFiles(dir, files);
  sh("git", ["init", "-q"], { cwd: dir });
  sh("git", ["add", "-A"], { cwd: dir });
  sh("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], { cwd: dir });
  return dir;
}

export function writeFiles(dir, files) {
  for (const [rel, content] of Object.entries(files)) {
    const f = path.join(dir, rel);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, content);
  }
}

/** Run the CLI with an isolated HOME and UNDOKEY_HOME. */
export function cli(args, { home, cwd, input, env = {} } = {}) {
  const r = sh(process.execPath, [BIN, ...args], {
    cwd,
    input,
    env: { ...process.env, HOME: home, UNDOKEY_HOME: path.join(home, ".undokey"), NO_COLOR: "1", ...env },
  });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

export function hook(agent, payload, opts) {
  return cli(["hook", agent], { ...opts, input: typeof payload === "string" ? payload : JSON.stringify(payload) });
}

export function listFiles(dir) {
  const out = [];
  const walk = (d) => {
    for (const n of fs.readdirSync(d)) {
      if (n === ".git") continue;
      const p = path.join(d, n);
      if (fs.statSync(p).isDirectory()) walk(p);
      else out.push(path.relative(dir, p));
    }
  };
  walk(dir);
  return out.sort();
}
