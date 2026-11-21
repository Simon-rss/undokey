/**
 * Normalize hook payloads from different agents into one shape.
 *
 * kind:
 *   "turn-start"  a user prompt is about to be processed (state = before the turn)
 *   "pre-tool"    a tool is about to run (any change seen here was made outside the agent)
 *   "post-tool"   a tool finished (any change seen here was made by that tool)
 *   "turn-end"    the agent finished its turn
 *   "other"
 *
 * @typedef {{
 *   agent: string, kind: string, event: string, tool: string,
 *   session: string, turn: string, toolUseId: string,
 *   cwd: string, transcript: string, prompt: string
 * }} AgentEvent
 */

const KIND = {
  UserPromptSubmit: "turn-start",
  PreInvocation: "pre-tool", // Antigravity: before every model call
  PreToolUse: "pre-tool",
  PostToolUse: "post-tool",
  Stop: "turn-end",
  SessionStart: "other",
};

const str = (v) => (v === undefined || v === null ? "" : String(v));

/**
 * @param {string} agent
 * @param {Record<string, any>} p raw payload
 * @returns {AgentEvent}
 */
export function normalize(agent, p) {
  if (agent === "antigravity") {
    const event = str(p.hookEventName);
    return {
      agent,
      event,
      kind: KIND[event] || "other",
      tool: str(p.toolCall?.name),
      session: str(p.conversationId),
      turn: "",
      toolUseId: str(p.toolCall?.id),
      cwd: str(p.cwd || (p.workspacePaths && p.workspacePaths[0])),
      transcript: str(p.transcriptPath),
      prompt: str(p.lastUserInput),
    };
  }
  // Codex, Claude Code and our OpenCode plugin all use the snake_case schema.
  const event = str(p.hook_event_name);
  return {
    agent,
    event,
    kind: KIND[event] || "other",
    tool: str(p.tool_name),
    session: str(p.session_id),
    turn: str(p.turn_id || p.prompt_id),
    toolUseId: str(p.tool_use_id),
    cwd: str(p.cwd),
    transcript: str(p.transcript_path),
    prompt: str(p.prompt),
  };
}

/** Human summary for a checkpoint created while handling `e`. */
export function summarize(e) {
  const who = e.agent + (e.tool ? ` · ${e.tool}` : "");
  switch (e.kind) {
    case "post-tool":
      return who;
    case "pre-tool":
      return `outside change (seen before ${who})`;
    case "turn-start":
      return `outside change (before ${e.agent} turn)`;
    case "turn-end":
      return `${e.agent} · end of turn`;
    default:
      return `${e.agent} · ${e.event || "event"}`;
  }
}

/** What an agent expects on stdout from a hook command. */
export function hookStdout(agent) {
  // Antigravity requires a JSON object on stdout; the others accept empty output.
  return agent === "antigravity" ? "{}\n" : "";
}
