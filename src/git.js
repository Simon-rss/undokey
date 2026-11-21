import { spawnSync } from "node:child_process";

/**
 * Run git synchronously.
 * @param {string[]} args
 * @param {{ env?: Record<string,string|undefined>, cwd?: string, input?: string|Buffer, allowFail?: boolean }} [opts]
 * @returns {{ code: number, stdout: string, stderr: string }}
 */
export function git(args, opts = {}) {
  const env = { ...process.env };
  // Never inherit git env from a parent process (e.g. when run inside a git hook).
  for (const k of ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR"]) delete env[k];
  Object.assign(env, {
    GIT_TERMINAL_PROMPT: "0",
    GIT_AUTHOR_NAME: "undokey",
    GIT_AUTHOR_EMAIL: "undokey@localhost",
    GIT_COMMITTER_NAME: "undokey",
    GIT_COMMITTER_EMAIL: "undokey@localhost",
  });
  for (const [k, v] of Object.entries(opts.env || {})) {
    if (v === undefined) delete env[k];
    else env[k] = v;
  }
  const r = spawnSync("git", args, {
    cwd: opts.cwd,
    env,
    input: opts.input,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
  });
  if (r.error) throw r.error;
  const res = { code: r.status ?? 1, stdout: r.stdout || "", stderr: r.stderr || "" };
  if (res.code !== 0 && !opts.allowFail) {
    const err = new Error(`git ${args.join(" ")} failed (${res.code}): ${res.stderr.trim()}`);
    // @ts-ignore
    err.result = res;
    throw err;
  }
  return res;
}

/** Resolve the top-level of the real git work tree containing `dir`, or null. */
export function realRepoRoot(dir) {
  const r = git(["rev-parse", "--show-toplevel"], { cwd: dir, allowFail: true });
  if (r.code !== 0) return null;
  return r.stdout.trim() || null;
}
