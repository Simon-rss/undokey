import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

/**
 * Group recorded hook events into agent turns.
 * @param {Record<string, any>[]} events
 */
export function turnsFromEvents(events) {
  /** @type {Map<string, any>} */
  const map = new Map();
  /** @type {Map<string, string>} last turn id per agent+session (for agents whose tool events carry no turn id) */
  const lastTurn = new Map();
  for (const ev0 of events) {
    const sessKey = `${ev0.agent}\u0000${ev0.session}`;
    let turnId = ev0.turn;
    if (!turnId && ev0.kind === "turn-start") turnId = `t${ev0.ts}`;
    if (!turnId) turnId = lastTurn.get(sessKey) || "";
    if (!turnId) continue;
    if (ev0.kind === "turn-start" || ev0.turn) lastTurn.set(sessKey, turnId);
    const ev = { ...ev0, turn: turnId };
    const key = `${ev.agent}\u0000${ev.session}\u0000${ev.turn}`;
    let t = map.get(key);
    if (!t) {
      t = {
        agent: ev.agent,
        session: ev.session,
        turn: ev.turn,
        startedAt: ev.ts,
        endedAt: ev.ts,
        before: null,
        after: null,
        prompt: "",
        transcript: ev.transcript || "",
        completed: false,
        tools: 0,
        synthetic: !ev0.turn,
      };
      map.set(key, t);
    }
    t.endedAt = ev.ts;
    if (ev.transcript) t.transcript = ev.transcript;
    if (ev.prompt && !t.prompt) t.prompt = ev.prompt;
    if (ev.kind === "turn-start" && !t.before) t.before = ev.commit;
    if (!t.before) t.before = ev.commit; // first event we saw for this turn
    if (ev.kind === "post-tool") t.tools++;
    t.after = ev.commit;
    if (ev.kind === "turn-end") t.completed = true;
  }
  const list = [...map.values()].sort((a, b) => a.startedAt - b.startedAt);
  list.forEach((t, i) => (t.index = i + 1));
  return list;
}

/** RFC 9562 UUIDv7 (time-ordered), the format Codex uses for session ids. */
export function uuidv7() {
  const b = crypto.randomBytes(16);
  const ms = BigInt(Date.now());
  for (let i = 0; i < 6; i++) b[i] = Number((ms >> BigInt(8 * (5 - i))) & 0xffn);
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = b.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function ts() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
}

/**
 * Fork a Codex session so that it ends right before (or right after) a given turn.
 * The original session file is never modified. Resume the result with `codex resume <newId>`.
 *
 * @param {string} rolloutPath  Codex rollout .jsonl (the hook's transcript_path)
 * @param {string} turnId
 * @param {"before" | "after"} where
 * @returns {{ newId: string, file: string, keptLines: number }}
 */
export function forkCodexSession(rolloutPath, turnId, where) {
  if (!rolloutPath || !fs.existsSync(rolloutPath)) {
    throw new Error(`Codex session file not found: ${rolloutPath || "(unknown)"}. Was it archived or deleted?`);
  }
  const lines = fs.readFileSync(rolloutPath, "utf8").split("\n").filter(Boolean);
  const parsed = lines.map((l) => {
    try {
      return JSON.parse(l);
    } catch {
      return {};
    }
  });
  const meta = parsed[0];
  const oldId = meta?.type === "session_meta" ? meta.payload?.id || meta.payload?.session_id : null;
  if (!oldId) throw new Error("Unrecognized Codex session format (first line is not session_meta).");

  const ptype = (e) => e?.payload?.type;
  const pturn = (e) => e?.payload?.turn_id;
  let cut;
  if (where === "before") {
    cut = parsed.findIndex((e) => ptype(e) === "task_started" && pturn(e) === turnId);
    if (cut === -1) throw new Error(`Turn ${turnId} not found in ${path.basename(rolloutPath)}.`);
    const keepsAnyTurn = parsed.slice(0, cut).some((e) => ptype(e) === "task_complete");
    if (!keepsAnyTurn) throw new Error("This is the first turn of the session: nothing to keep. Just start a new Codex session.");
  } else {
    const end = parsed.findIndex((e) => ptype(e) === "task_complete" && pturn(e) === turnId);
    if (end === -1) throw new Error(`Turn ${turnId} did not complete in ${path.basename(rolloutPath)}; cannot rewind to its end.`);
    cut = end + 1;
  }

  const newId = uuidv7();
  const kept = lines.slice(0, cut).map((l) => l.split(oldId).join(newId));
  const file = path.join(path.dirname(rolloutPath), `rollout-${ts()}-${newId}.jsonl`);
  fs.writeFileSync(file, kept.join("\n") + "\n");
  return { newId, file, keptLines: kept.length };
}
