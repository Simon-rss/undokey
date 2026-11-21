import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { Store } from "../src/store.js";
import { makeRepo, tmpdir, writeFiles, listFiles, sh } from "./helpers.js";

const status = (dir) => sh("git", ["status", "--porcelain"], { cwd: dir }).stdout;

test("snapshot only creates a checkpoint when files change", () => {
  const repo = makeRepo();
  const s = new Store(repo, tmpdir());
  const a = s.snapshotLocked("first");
  assert.equal(a.changed, true);
  const b = s.snapshotLocked("again");
  assert.equal(b.changed, false);
  assert.equal(b.commit, a.commit);
  writeFiles(repo, { "a.txt": "changed\n" });
  const c = s.snapshotLocked("edit", { agent: "codex" });
  assert.equal(c.changed, true);
  const entries = s.list();
  assert.equal(entries.length, 2);
  assert.equal(entries[0].summary, "edit");
  assert.equal(entries[0].meta.agent, "codex");
  assert.equal(entries[1].summary, "baseline (first checkpoint)");
});

test("restore makes the work tree match exactly (edits, additions, deletions) and is undoable", () => {
  const repo = makeRepo({ "a.txt": "hi\n", "keep/b.txt": "b\n", "gone.md": "x\n" });
  const s = new Store(repo, tmpdir());
  const base = s.snapshotLocked("base").commit;
  writeFiles(repo, { "a.txt": "edited\n", "new/c.txt": "c\n" });
  fs.rmSync(path.join(repo, "gone.md"));
  const after = s.snapshotLocked("agent").commit;

  s.restore(base);
  assert.deepEqual(listFiles(repo), ["a.txt", "gone.md", "keep/b.txt"]);
  assert.equal(fs.readFileSync(path.join(repo, "a.txt"), "utf8"), "hi\n");
  assert.equal(fs.existsSync(path.join(repo, "new")), false, "empty dirs left by removed files are cleaned up");

  s.restore(after);
  assert.deepEqual(listFiles(repo), ["a.txt", "keep/b.txt", "new/c.txt"]);
  assert.equal(fs.readFileSync(path.join(repo, "a.txt"), "utf8"), "edited\n");
});

test("never touches the project's own .git index or history", () => {
  const repo = makeRepo();
  writeFiles(repo, { "wip.txt": "uncommitted\n" });
  sh("git", ["add", "wip.txt"], { cwd: repo }); // something staged in the real index
  const before = status(repo);
  const head = sh("git", ["rev-parse", "HEAD"], { cwd: repo }).stdout;
  const s = new Store(repo, tmpdir());
  const base = s.snapshotLocked("base").commit;
  writeFiles(repo, { "wip.txt": "agent changed\n" });
  s.snapshotLocked("edit");
  s.restore(base);
  assert.equal(status(repo), before);
  assert.equal(sh("git", ["rev-parse", "HEAD"], { cwd: repo }).stdout, head);
  assert.equal(sh("git", ["stash", "list"], { cwd: repo }).stdout, "");
});

test("keeps files that are tracked but match .gitignore (force-added)", () => {
  const repo = makeRepo({ ".gitignore": "prs\n", "a.txt": "a\n" });
  writeFiles(repo, { "prs/README.md": "keep me\n" });
  sh("git", ["add", "-f", "prs/README.md"], { cwd: repo });
  sh("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "force"], { cwd: repo });
  const s = new Store(repo, tmpdir());
  const base = s.snapshotLocked("base").commit;
  fs.rmSync(path.join(repo, "prs/README.md"));
  s.snapshotLocked("deleted");
  s.restore(base);
  assert.equal(fs.readFileSync(path.join(repo, "prs/README.md"), "utf8"), "keep me\n");
});

test("ignored files (node_modules, build output) are neither recorded nor deleted", () => {
  const repo = makeRepo({ ".gitignore": "node_modules/\n", "a.txt": "a\n" });
  const s = new Store(repo, tmpdir());
  const base = s.snapshotLocked("base").commit;
  writeFiles(repo, { "node_modules/pkg/index.js": "x\n", "a.txt": "b\n" });
  s.snapshotLocked("install");
  s.restore(base);
  assert.equal(fs.existsSync(path.join(repo, "node_modules/pkg/index.js")), true);
  assert.equal(fs.readFileSync(path.join(repo, "a.txt"), "utf8"), "a\n");
});

test("respects the real repo's .git/info/exclude", () => {
  const repo = makeRepo();
  fs.appendFileSync(path.join(repo, ".git/info/exclude"), "secret.env\n");
  writeFiles(repo, { "secret.env": "TOKEN=1\n" });
  const s = new Store(repo, tmpdir());
  const base = s.snapshotLocked("base").commit;
  const files = s.g(["ls-tree", "-r", "--name-only", base]).stdout.split("\n").filter(Boolean);
  assert.deepEqual(files, ["a.txt"]);
});

test("concurrent hooks (parallel tool calls) do not lose checkpoints", async () => {
  const repo = makeRepo();
  const home = tmpdir();
  const storeUrl = new URL("../src/store.js", import.meta.url).href;
  const runs = Array.from({ length: 8 }, (_, i) =>
    new Promise((resolve, reject) => {
      fs.writeFileSync(path.join(repo, `f${i}.txt`), `${i}\n`);
      const code = `import { Store } from ${JSON.stringify(storeUrl)}; new Store(${JSON.stringify(repo)}, ${JSON.stringify(home)}).snapshotLocked("p${i}");`;
      const p = spawn(process.execPath, ["--input-type=module", "-e", code], { stdio: ["ignore", "ignore", "pipe"] });
      let err = "";
      p.stderr.on("data", (d) => (err += d));
      p.on("close", (c) => (c === 0 ? resolve(null) : reject(new Error(err))));
    }),
  );
  await Promise.all(runs);
  const s = new Store(repo, home);
  const files = s.g(["ls-tree", "--name-only", s.head()]).stdout.split("\n").filter(Boolean);
  assert.deepEqual(files.sort(), ["a.txt", ...Array.from({ length: 8 }, (_, i) => `f${i}.txt`)].sort());
});

test("diff shows what one step changed, or checkpoint vs current files", () => {
  const repo = makeRepo();
  const s = new Store(repo, tmpdir());
  s.snapshotLocked("base");
  writeFiles(repo, { "a.txt": "hello world\n" });
  const step = s.snapshotLocked("edit").commit;
  assert.match(s.diff(step), /\+hello world/);
  writeFiles(repo, { "a.txt": "later\n" });
  assert.match(s.diff(step, { against: "now" }), /\+later/);
});
