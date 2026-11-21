#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Store, undokeyHome } from "../src/store.js";
import { resolveWorktree, readConfig, writeConfig } from "../src/workspace.js";
import { handleHook } from "../src/hook.js";
import { AGENTS, detectAgents, install, uninstall, isInstalled } from "../src/install.js";
import { turnsFromEvents, forkCodexSession } from "../src/turns.js";
import { git, realRepoRoot } from "../src/git.js";

const pkg = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "package.json"), "utf8"));
const tty = process.stdout.isTTY && !process.env.NO_COLOR;
const c = (code) => (s) => (tty ? `\x1b[${code}m${s}\x1b[0m` : String(s));
const dim = c(2), bold = c(1), green = c(32), yellow = c(33), red = c(31), cyan = c(36);

const HELP = `${bold("undokey")} ${pkg.version} — a universal undo key for AI coding agents

${bold("Setup")}
  undokey init [--agent codex,claude,opencode,antigravity] [--project]
                              install hooks (default: every detected agent, user-wide)
  undokey uninstall [--agent ...] [--project]
  undokey status              what is installed, and what is recorded for this project

${bold("Everyday")}
  undokey log [-n 30]         timeline of checkpoints for the current project
  undokey diff <id> [--now] [--stat]
                              what a step changed (or: checkpoint vs. current files)
  undokey undo [n]            step back n checkpoints (default 1); always redoable
  undokey to <id>             make the files exactly match a checkpoint; always redoable

${bold("Turns")}
  undokey turns               agent turns (prompt -> tool calls -> end) for this project
  undokey rewind <turn> [--after] [--chat]
                              restore files to before <turn> (or after it with --after);
                              --chat (Codex): also fork the conversation to that point

${bold("Other")}
  undokey snapshot [dir] [--label text]   manual checkpoint
  undokey track [dir] / untrack [dir]     record a directory that is not a git repo
  undokey hook <agent>                    (internal) called by agent hooks

Data lives in ${dim(undokeyHome())}; your project's own .git is never touched.`;

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const [k, v] = a.slice(2).split("=");
      if (v !== undefined) args[k] = v;
      else if (i + 1 < argv.length && !argv[i + 1].startsWith("-") && ["agent", "label", "n"].includes(k)) args[k] = argv[++i];
      else args[k] = true;
    } else if (a === "-n") args.n = argv[++i];
    else if (a === "-h") args.help = true;
    else args._.push(a);
  }
  return args;
}

function die(msg) {
  console.error(red(`undokey: ${msg}`));
  process.exit(1);
}

function storeHere(dir = process.cwd()) {
  const wt = resolveWorktree(dir);
  if (!wt) die(`${dir} is not inside a git repository (use \`undokey track\` to record a plain directory).`);
  const store = new Store(wt);
  return store;
}

function requireHistory(store) {
  if (!store.head()) die(`nothing recorded yet for ${store.worktree}. Run \`undokey init\`, then use your agent as usual.`);
}

const time = (d) => d.toTimeString().slice(0, 8);
const day = (d) => d.toISOString().slice(0, 10);
const pad = (s, n) => {
  s = String(s);
  const w = [...s].reduce((acc, ch) => acc + (ch.charCodeAt(0) > 0x2e80 ? 2 : 1), 0);
  return w >= n ? s : s + " ".repeat(n - w);
};
const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

function selectedAgents(args) {
  if (args.agent && args.agent !== true) {
    const list = String(args.agent).split(",").map((s) => s.trim());
    for (const a of list) if (!AGENTS.includes(a)) die(`unknown agent "${a}" (choose from ${AGENTS.join(", ")})`);
    return list;
  }
  const found = detectAgents();
  const list = AGENTS.filter((a) => found[a]);
  if (!list.length) die(`no supported agent found. Use --agent to choose from ${AGENTS.join(", ")}.`);
  return list;
}

function projectRootOrDie() {
  const root = realRepoRoot(process.cwd());
  if (!root) die("--project needs to run inside a git repository.");
  return root;
}

const PROJECT_FILES = [".codex/hooks.json", ".claude/settings.local.json", ".agents/hooks.json", ".opencode/plugin/undokey.js"];

function excludeProjectFiles(root) {
  const r = git(["rev-parse", "--git-path", "info/exclude"], { cwd: root, allowFail: true });
  if (r.code !== 0) return;
  const file = path.resolve(root, r.stdout.trim());
  const cur = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
  const missing = PROJECT_FILES.filter((p) => !cur.split("\n").includes(p));
  if (!missing.length) return;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFileSync(file, `${cur && !cur.endsWith("\n") ? "\n" : ""}# undokey (local hook config)\n${missing.join("\n")}\n`);
}

function cmdInit(args) {
  const scope = args.project ? "project" : "global";
  const root = args.project ? projectRootOrDie() : undefined;
  for (const agent of selectedAgents(args)) {
    try {
      const r = install(agent, scope, root);
      console.log(`${green("✓")} ${pad(agent, 12)} ${r.changed ? "installed" : "already installed"}  ${dim(r.file)}`);
      if (r.backup) console.log(`  ${dim(`backup: ${r.backup}`)}`);
      if (r.note) console.log(`  ${yellow("!")} ${r.note}`);
    } catch (e) {
      console.log(`${red("✗")} ${pad(agent, 12)} ${/** @type {Error} */ (e).message}`);
    }
  }
  if (root) excludeProjectFiles(root);
  console.log(`\nDone. Use your agent as usual, then try ${cyan("undokey log")} inside the project.`);
}

function cmdUninstall(args) {
  const scope = args.project ? "project" : "global";
  const root = args.project ? projectRootOrDie() : undefined;
  const list = args.agent ? selectedAgents(args) : AGENTS;
  for (const agent of list) {
    try {
      const r = uninstall(agent, scope, root);
      if (r.changed) console.log(`${green("✓")} ${pad(agent, 12)} removed  ${dim(r.file)}`);
    } catch (e) {
      console.log(`${red("✗")} ${pad(agent, 12)} ${/** @type {Error} */ (e).message}`);
    }
  }
  console.log(dim(`Recorded checkpoints are kept in ${undokeyHome()}.`));
}

function cmdStatus() {
  const found = detectAgents();
  const root = realRepoRoot(process.cwd());
  console.log(bold("Agents"));
  for (const a of AGENTS) {
    const g = isInstalled(a, "global");
    const p = root ? isInstalled(a, "project", root) : false;
    const where = [g && "user-wide", p && "this project"].filter(Boolean).join(" + ") || "not installed";
    console.log(`  ${pad(a, 12)} ${found[a] ? "found" : dim("not found")}   ${g || p ? green(where) : dim(where)}`);
  }
  const wt = resolveWorktree(process.cwd());
  console.log(bold("\nThis project"));
  if (!wt) return console.log(`  ${dim("not recorded (not a git repo; see `undokey track`)")}`);
  const store = new Store(wt);
  const n = store.head() ? Number(store.g(["rev-list", "--count", "refs/heads/timeline"]).stdout.trim()) : 0;
  const turns = turnsFromEvents(store.readEvents()).length;
  console.log(`  ${wt}\n  ${n} checkpoints, ${turns} turns  ${dim(store.dir)}`);
  const errs = path.join(undokeyHome(), "errors.log");
  if (fs.existsSync(errs) && fs.statSync(errs).size > 0) console.log(`  ${yellow("!")} hook errors logged in ${errs}`);
}

/** id the work tree currently matches (resolves restore commits to their target) */
function effectiveHead(store) {
  const head = store.head();
  if (!head) return null;
  const e = store.entry(head);
  return e.meta.kind === "restore" && e.meta.target ? e.meta.target : head;
}

function cmdLog(args) {
  const store = storeHere();
  requireHistory(store);
  const n = Number(args.n) || 30;
  const entries = store.list(n);
  const current = effectiveHead(store);
  let lastDay = "";
  for (const e of entries) {
    const d = day(e.time);
    if (d !== lastDay) {
      console.log(dim(`── ${d}`));
      lastDay = d;
    }
    const kind = e.meta.kind;
    const color =
      kind === "restore" ? cyan : kind === "pre-restore" || /^outside change/.test(e.summary) ? yellow : (s) => s;
    const what = color(pad(trunc(e.summary, 48), 48));
    const stat = e.files ? `${e.files} file${e.files > 1 ? "s" : ""} ${green(`+${e.insertions}`)} ${red(`-${e.deletions}`)}` : "";
    const mark = e.id === current ? green("●") : " ";
    console.log(`${mark} ${yellow(e.id.slice(0, 8))}  ${dim(time(e.time))}  ${what}  ${stat}`);
  }
  console.log(dim(`\n● = current files.  undokey diff <id> · undokey to <id> · undokey undo`));
}

function cmdDiff(args) {
  const store = storeHere();
  requireHistory(store);
  const id = args._[1];
  if (!id) die("usage: undokey diff <id> [--now] [--stat]");
  const out = store.diff(id, { against: args.now ? "now" : "parent", stat: !!args.stat, color: tty });
  process.stdout.write(out || dim("(no changes)\n"));
}

function restoreAndReport(store, target, label) {
  const from = effectiveHead(store);
  const r = store.restore(target, { label });
  console.log(`${green("✓")} files now match ${yellow(r.target.slice(0, 8))}${label ? ` (${label})` : ""}`);
  if (from && from !== r.target) console.log(dim(`  changed your mind? undokey to ${from.slice(0, 8)}`));
  return r;
}

function cmdTo(args) {
  const store = storeHere();
  requireHistory(store);
  const id = args._[1];
  if (!id) die("usage: undokey to <id>");
  restoreAndReport(store, id);
}

function cmdUndo(args) {
  const store = storeHere();
  requireHistory(store);
  const n = Number(args._[1] || 1);
  if (!Number.isInteger(n) || n < 1) die("usage: undokey undo [n]");
  let id = effectiveHead(store);
  const undone = [];
  for (let i = 0; i < n; i++) {
    const e = store.entry(id);
    if (!e.parent) die(i === 0 ? "already at the first checkpoint; nothing to undo." : `only ${i} step(s) available to undo.`);
    undone.push(e);
    id = e.parent;
  }
  for (const e of undone) console.log(dim(`  undo ${e.id.slice(0, 8)} ${e.summary}`));
  restoreAndReport(store, id);
}

function cmdTurns() {
  const store = storeHere();
  const turns = turnsFromEvents(store.readEvents());
  if (!turns.length) return console.log(dim("No agent turns recorded yet for this project."));
  for (const t of turns) {
    const status = t.completed ? "" : yellow(" (unfinished)");
    console.log(
      `${bold(pad(`#${t.index}`, 5))}${pad(t.agent, 12)}${dim(time(new Date(t.startedAt)))}  ${pad(`${t.tools} tools`, 9)} ${trunc(
        (t.prompt || dim("(no prompt captured)")).replace(/\s+/g, " "),
        60,
      )}${status}`,
    );
  }
  console.log(dim(`\nundokey rewind <#> [--after] [--chat]`));
}

function findTurn(turns, ref) {
  const s = String(ref).replace(/^#/, "");
  if (/^\d+$/.test(s) && Number(s) <= turns.length) return turns[Number(s) - 1];
  return turns.find((t) => t.turn === s || t.turn.startsWith(s));
}

function cmdRewind(args) {
  const store = storeHere();
  const turns = turnsFromEvents(store.readEvents());
  if (!args._[1]) die("usage: undokey rewind <turn#|turnId> [--after] [--chat]");
  const t = findTurn(turns, args._[1]);
  if (!t) die(`turn ${args._[1]} not found (see \`undokey turns\`).`);
  const where = args.after ? "after" : "before";
  const target = where === "before" ? t.before : t.after;
  if (!target) die("no checkpoint recorded for that turn.");

  let fork = null;
  if (args.chat) {
    if (t.agent !== "codex" || t.synthetic) {
      die(
        t.agent === "claude"
          ? "Claude Code can rewind its own conversation: use /rewind in Claude Code (undokey restores files)."
          : `--chat is currently supported for Codex only (this turn is from ${t.agent}).`,
      );
    }
    try {
      fork = forkCodexSession(t.transcript, t.turn, where);
    } catch (e) {
      die(/** @type {Error} */ (e).message);
    }
  }
  restoreAndReport(store, target, `${where} turn #${t.index}`);
  if (fork) {
    console.log(`${green("✓")} conversation forked to ${where} turn #${t.index} (original session untouched)`);
    console.log(`  continue with: ${cyan(`codex resume ${fork.newId}`)}`);
  }
}

function cmdSnapshot(args) {
  const dir = args._[1] ? path.resolve(args._[1]) : process.cwd();
  const wt = resolveWorktree(dir) || (fs.existsSync(dir) ? fs.realpathSync(dir) : null);
  if (!wt) die(`${dir} does not exist`);
  const store = new Store(wt);
  const label = args.label && args.label !== true ? String(args.label) : "manual checkpoint";
  const r = store.snapshotLocked(label, { kind: label === "baseline" ? "baseline" : "manual" });
  if (!args.quiet) console.log(r.changed ? `${green("✓")} checkpoint ${yellow(r.commit.slice(0, 8))}` : dim(`unchanged (${r.commit.slice(0, 8)})`));
}

function cmdTrack(args, on) {
  const dir = fs.realpathSync(args._[1] ? path.resolve(args._[1]) : process.cwd());
  const cfg = readConfig();
  cfg.track = cfg.track.filter((p) => p !== dir);
  if (on) cfg.track.push(dir);
  writeConfig(cfg);
  console.log(on ? `${green("✓")} recording ${dir}` : `${green("✓")} no longer recording ${dir}`);
}

function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0];
try {
  if (args.version || cmd === "version") console.log(pkg.version);
  else if (!cmd || args.help || cmd === "help") console.log(HELP);
  else if (cmd === "hook") {
    const out = handleHook(args._[1] || "unknown", readStdin());
    if (out) process.stdout.write(out);
    process.exit(0);
  } else if (cmd === "init") cmdInit(args);
  else if (cmd === "uninstall") cmdUninstall(args);
  else if (cmd === "status" || cmd === "doctor") cmdStatus();
  else if (cmd === "log") cmdLog(args);
  else if (cmd === "diff" || cmd === "show") cmdDiff(args);
  else if (cmd === "to" || cmd === "restore") cmdTo(args);
  else if (cmd === "undo") cmdUndo(args);
  else if (cmd === "turns") cmdTurns();
  else if (cmd === "rewind") cmdRewind(args);
  else if (cmd === "snapshot") cmdSnapshot(args);
  else if (cmd === "track") cmdTrack(args, true);
  else if (cmd === "untrack") cmdTrack(args, false);
  else die(`unknown command "${cmd}". Run \`undokey help\`.`);
} catch (e) {
  if (cmd === "hook") process.exit(0);
  die(/** @type {Error} */ (e).message);
}
