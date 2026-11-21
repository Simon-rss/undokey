import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { realRepoRoot } from "./git.js";
import { undokeyHome } from "./store.js";

function configPath(home = undokeyHome()) {
  return path.join(home, "config.json");
}

/** @returns {{ track: string[], ignore: string[] }} */
export function readConfig(home = undokeyHome()) {
  try {
    const c = JSON.parse(fs.readFileSync(configPath(home), "utf8"));
    return { track: c.track || [], ignore: c.ignore || [] };
  } catch {
    return { track: [], ignore: [] };
  }
}

export function writeConfig(cfg, home = undokeyHome()) {
  fs.mkdirSync(home, { recursive: true });
  fs.writeFileSync(configPath(home), JSON.stringify(cfg, null, 2) + "\n");
}

function isInside(child, parent) {
  const rel = path.relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/** Directories that belong to agents/tools themselves; never snapshot them. */
function builtinIgnores() {
  const h = os.homedir();
  return [".codex", ".claude", ".gemini", ".config", ".local", ".cache", ".npm", ".undokey", "Library"].map((d) =>
    path.join(h, d),
  );
}

/**
 * Decide which work tree (if any) an agent event in `cwd` should be recorded against.
 * - inside a git repo -> the repo's top level
 * - inside a directory registered with `undokey track` -> that directory
 * - otherwise -> null (not recorded)
 * @param {string} cwd
 * @returns {string | null}
 */
export function resolveWorktree(cwd, home = undokeyHome()) {
  if (!cwd || !fs.existsSync(cwd)) return null;
  let real;
  try {
    real = fs.realpathSync(cwd);
  } catch {
    return null;
  }
  const cfg = readConfig(home);
  const homeDir = fs.realpathSync(os.homedir());
  const ignores = [...builtinIgnores(), ...cfg.ignore].map((p) => {
    try {
      return fs.realpathSync(p);
    } catch {
      return p;
    }
  });
  if (ignores.some((p) => isInside(real, p))) return null;

  let root = realRepoRoot(real);
  if (!root) {
    const tracked = cfg.track
      .map((p) => {
        try {
          return fs.realpathSync(p);
        } catch {
          return null;
        }
      })
      .filter((p) => p && isInside(real, p))
      .sort((a, b) => b.length - a.length);
    root = tracked[0] || null;
  }
  if (!root) return null;
  root = fs.realpathSync(root);
  if (root === homeDir || root === path.parse(root).root) return null;
  return root;
}
