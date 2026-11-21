import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { normalize, summarize, hookStdout } from "./events.js";
import { resolveWorktree } from "./workspace.js";
import { Store, undokeyHome } from "./store.js";
import { git } from "./git.js";

const BIN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "undokey.js");
const INLINE_BASELINE_MAX_FILES = 3000;

function logError(home, msg) {
  try {
    fs.mkdirSync(home, { recursive: true });
    fs.appendFileSync(path.join(home, "errors.log"), `${new Date().toISOString()} ${msg}\n`);
  } catch {}
}

function trackedFileCount(worktree) {
  const r = git(["ls-files", "-z"], { cwd: worktree, allowFail: true });
  if (r.code !== 0) return 0;
  return r.stdout.split("\0").length - 1;
}

/**
 * Entry point for `undokey hook <agent>`. Must never break the agent:
 * every error is swallowed and logged; the process always exits 0.
 * @param {string} agent @param {string} raw stdin
 * @returns {string} what to print on stdout
 */
export function handleHook(agent, raw, home = undokeyHome()) {
  try {
    const payload = raw && raw.trim() ? JSON.parse(raw) : {};
    const e = normalize(agent, payload);
    const worktree = resolveWorktree(e.cwd, home);
    if (!worktree) return hookStdout(agent);
    const store = new Store(worktree, home);

    if (!store.exists() && trackedFileCount(worktree) > INLINE_BASELINE_MAX_FILES) {
      // Big repo: build the first snapshot in the background so the agent is not blocked.
      const spawned = store.withLock(() => {
        if (store.exists()) return false;
        store.init();
        return true;
      });
      if (spawned) {
        const child = spawn(process.execPath, [BIN, "snapshot", worktree, "--label", "baseline", "--quiet"], {
          detached: true,
          stdio: "ignore",
          env: { ...process.env, UNDOKEY_HOME: home },
        });
        child.unref();
        return hookStdout(agent);
      }
    }

    const res = store.snapshotLocked(summarize(e), {
      agent: e.agent,
      event: e.event,
      kind: e.kind,
      tool: e.tool,
      session: e.session,
      turn: e.turn,
      toolUseId: e.toolUseId,
    });
    store.appendEvent({
      ts: Date.now(),
      agent: e.agent,
      event: e.event,
      kind: e.kind,
      tool: e.tool,
      session: e.session,
      turn: e.turn,
      toolUseId: e.toolUseId,
      transcript: e.transcript,
      prompt: e.prompt ? e.prompt.slice(0, 200) : "",
      commit: res.commit,
      changed: res.changed,
    });
  } catch (err) {
    logError(home, `hook ${agent}: ${/** @type {Error} */ (err).stack || err}`);
  }
  return hookStdout(agent);
}
