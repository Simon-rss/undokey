import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { git, realRepoRoot } from "./git.js";

export const TIMELINE_REF = "refs/heads/timeline";
const META_PREFIX = "undokey: ";

export function undokeyHome() {
  return process.env.UNDOKEY_HOME || path.join(os.homedir(), ".undokey");
}

export function keyFor(worktree) {
  return crypto.createHash("sha1").update(worktree).digest("hex").slice(0, 16);
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * A shadow git repository that records snapshots of one work tree.
 * It never touches the work tree's own .git directory or index.
 */
export class Store {
  /** @param {string} worktree @param {string} [home] */
  constructor(worktree, home = undokeyHome()) {
    this.worktree = fs.realpathSync(worktree);
    this.home = home;
    const key = keyFor(this.worktree);
    this.dir = path.join(home, "repos", `${key}.git`);
    this.lockDir = path.join(home, "repos", `${key}.lock`);
    this.eventsFile = path.join(this.dir, "undokey-events.jsonl");
    this.env = {
      GIT_DIR: this.dir,
      GIT_WORK_TREE: this.worktree,
      GIT_INDEX_FILE: path.join(this.dir, "undokey-index"),
    };
  }

  exists() {
    return fs.existsSync(path.join(this.dir, "HEAD"));
  }

  /** git against the shadow repo */
  g(args, opts = {}) {
    return git(args, { ...opts, env: { ...this.env, ...(opts.env || {}) }, cwd: this.worktree });
  }

  init() {
    if (this.exists()) return;
    fs.mkdirSync(path.dirname(this.dir), { recursive: true });
    git(["init", "-q", "--bare", this.dir]);
    const cfg = (k, v) => git(["--git-dir", this.dir, "config", k, v]);
    cfg("core.autocrlf", "false");
    cfg("core.quotepath", "false");
    cfg("gc.auto", "0");
    fs.writeFileSync(
      path.join(this.dir, "undokey-meta.json"),
      JSON.stringify({ worktree: this.worktree, createdAt: new Date().toISOString() }, null, 2),
    );
  }

  /**
   * Serialize access: agents fire hooks concurrently for parallel tool calls.
   * @template T @param {() => T} fn @param {number} [timeoutMs] @returns {T}
   */
  withLock(fn, timeoutMs = 15000) {
    fs.mkdirSync(path.dirname(this.lockDir), { recursive: true });
    const start = Date.now();
    for (;;) {
      try {
        fs.mkdirSync(this.lockDir);
        break;
      } catch (e) {
        if (/** @type {any} */ (e).code !== "EEXIST") throw e;
        try {
          const age = Date.now() - fs.statSync(this.lockDir).mtimeMs;
          if (age > 120000) fs.rmdirSync(this.lockDir); // stale lock from a killed process
        } catch {}
        if (Date.now() - start > timeoutMs) throw new Error(`undokey: timed out waiting for lock ${this.lockDir}`);
        sleep(25);
      }
    }
    try {
      return fn();
    } finally {
      try {
        fs.rmdirSync(this.lockDir);
      } catch {}
    }
  }

  head() {
    const r = this.g(["rev-parse", "-q", "--verify", TIMELINE_REF], { allowFail: true });
    return r.code === 0 ? r.stdout.trim() : null;
  }

  /** Copy the real repo's info/exclude so locally-excluded files are not snapshotted. */
  syncExcludes() {
    const real = realRepoRoot(this.worktree);
    if (!real) return;
    const r = git(["rev-parse", "--git-path", "info/exclude"], { cwd: this.worktree, allowFail: true });
    if (r.code !== 0) return;
    const src = path.resolve(this.worktree, r.stdout.trim());
    const dst = path.join(this.dir, "info", "exclude");
    try {
      const content = fs.readFileSync(src, "utf8");
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      if (!fs.existsSync(dst) || fs.readFileSync(dst, "utf8") !== content) fs.writeFileSync(dst, content);
    } catch {}
  }

  /** Stage the current work tree into the shadow index and return the tree id. */
  stage() {
    this.syncExcludes();
    this.g(["add", "-A", "--ignore-errors", "."], { allowFail: true });
    // Files tracked by the real repo but matching .gitignore (force-added) must be kept too.
    if (realRepoRoot(this.worktree)) {
      const ls = git(["ls-files", "-z", "-c", "-i", "--exclude-standard"], { cwd: this.worktree, allowFail: true });
      if (ls.code === 0 && ls.stdout.length > 0) {
        this.g(["add", "-f", "--ignore-errors", "--pathspec-from-file=-", "--pathspec-file-nul"], {
          input: ls.stdout,
          allowFail: true,
        });
      }
    }
    return this.g(["write-tree"]).stdout.trim();
  }

  /**
   * Record the work tree. Creates a commit only when something changed.
   * Caller must hold the lock (use snapshotLocked otherwise).
   * @param {string} summary @param {Record<string, any>} meta
   * @returns {{ commit: string, changed: boolean }}
   */
  snapshot(summary, meta = {}) {
    this.init();
    const tree = this.stage();
    const parent = this.head();
    if (parent) {
      const ptree = this.g(["rev-parse", `${parent}^{tree}`]).stdout.trim();
      if (ptree === tree) return { commit: parent, changed: false };
    } else {
      summary = "baseline (first checkpoint)";
      meta = { ...meta, kind: "baseline" };
    }
    const body = `${summary}\n\n${META_PREFIX}${JSON.stringify({ ts: Date.now(), ...meta })}\n`;
    const args = ["commit-tree", tree];
    if (parent) args.push("-p", parent);
    const commit = this.g(args, { input: body }).stdout.trim();
    this.g(["update-ref", TIMELINE_REF, commit, ...(parent ? [parent] : [])]);
    return { commit, changed: true };
  }

  snapshotLocked(summary, meta = {}) {
    return this.withLock(() => this.snapshot(summary, meta));
  }

  /** @param {string} rev */
  resolve(rev) {
    const r = this.g(["rev-parse", "-q", "--verify", `${rev}^{commit}`], { allowFail: true });
    if (r.code !== 0) throw new Error(`undokey: unknown checkpoint "${rev}"`);
    return r.stdout.trim();
  }

  /**
   * Make the work tree exactly match a checkpoint. The current state is saved first, so this is undoable.
   * @param {string} rev @param {Record<string, any>} [meta]
   */
  restore(rev, meta = {}) {
    return this.withLock(() => {
      this.init();
      const target = this.resolve(rev);
      const before = this.snapshot(`before restore to ${target.slice(0, 8)}`, { kind: "pre-restore", target });
      this.g(["read-tree", "-u", "--reset", target]);
      const after = this.snapshot(`restored to ${target.slice(0, 8)}`, { kind: "restore", target, ...meta });
      return { target, before: before.commit, after: after.commit };
    });
  }

  /**
   * Timeline entries, newest first.
   * @param {number} [limit]
   */
  list(limit = 30) {
    if (!this.head()) return [];
    const out = this.g([
      "log",
      `-n${limit}`,
      "--first-parent",
      "--format=%x1e%H%x1f%P%x1f%ct%x1f%B%x1f",
      "--shortstat",
      TIMELINE_REF,
    ]).stdout;
    return out
      .split("\x1e")
      .filter((s) => s.trim())
      .map((rec) => {
        const [id, parents, ct, message, stat = ""] = rec.split("\x1f");
        const lines = message.trim().split("\n");
        const metaLine = lines.find((l) => l.startsWith(META_PREFIX));
        let meta = {};
        try {
          meta = metaLine ? JSON.parse(metaLine.slice(META_PREFIX.length)) : {};
        } catch {}
        const m = stat.match(/(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?/);
        return {
          id,
          parent: parents.trim().split(" ")[0] || null,
          time: new Date(Number(ct) * 1000),
          summary: lines[0],
          meta,
          files: m ? Number(m[1]) : 0,
          insertions: m && m[2] ? Number(m[2]) : 0,
          deletions: m && m[3] ? Number(m[3]) : 0,
        };
      });
  }

  /** @param {string} rev */
  entry(rev) {
    const id = this.resolve(rev);
    const out = this.g(["log", "-n1", "--format=%P%x1f%ct%x1f%B", id]).stdout;
    const [parents, ct, message] = out.split("\x1f");
    const lines = message.trim().split("\n");
    const metaLine = lines.find((l) => l.startsWith(META_PREFIX));
    let meta = {};
    try {
      meta = metaLine ? JSON.parse(metaLine.slice(META_PREFIX.length)) : {};
    } catch {}
    return { id, parent: parents.trim().split(" ")[0] || null, time: new Date(Number(ct) * 1000), summary: lines[0], meta };
  }

  /**
   * @param {string} rev
   * @param {{ against?: "parent" | "now", stat?: boolean, color?: boolean }} [opts]
   */
  diff(rev, opts = {}) {
    const id = this.resolve(rev);
    const color = opts.color ? "--color=always" : "--color=never";
    const fmt = opts.stat ? ["--stat"] : [];
    if (opts.against === "now") {
      // compare checkpoint with the current work tree (staged into the shadow index, not committed)
      return this.withLock(() => {
        const tree = this.stage();
        return this.g(["diff", color, ...fmt, id, tree]).stdout;
      });
    }
    const e = this.entry(id);
    if (!e.parent) return this.g(["show", color, ...fmt, "--format=", id]).stdout;
    return this.g(["diff", color, ...fmt, e.parent, id]).stdout;
  }

  /** @param {Record<string, any>} event */
  appendEvent(event) {
    this.init();
    fs.appendFileSync(this.eventsFile, JSON.stringify(event) + "\n");
  }

  /** @returns {Record<string, any>[]} */
  readEvents() {
    if (!fs.existsSync(this.eventsFile)) return [];
    return fs
      .readFileSync(this.eventsFile, "utf8")
      .split("\n")
      .filter(Boolean)
      .map((l) => {
        try {
          return JSON.parse(l);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  }
}

/** All known stores under the home dir. */
export function listStores(home = undokeyHome()) {
  const dir = path.join(home, "repos");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((n) => n.endsWith(".git"))
    .map((n) => {
      try {
        const meta = JSON.parse(fs.readFileSync(path.join(dir, n, "undokey-meta.json"), "utf8"));
        return { key: n.slice(0, -4), worktree: meta.worktree, dir: path.join(dir, n) };
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}
