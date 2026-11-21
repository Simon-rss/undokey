from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

_KIND = {
    "UserPromptSubmit": "turn-start",
    "PreInvocation": "pre-tool",  # Antigravity: before every model call
    "PreToolUse": "pre-tool",
    "PostToolUse": "post-tool",
    "Stop": "turn-end",
    "SessionStart": "other",
}


def _s(v: Any) -> str:
    return "" if v is None else str(v)


@dataclass
class AgentEvent:
    agent: str
    kind: str
    event: str
    tool: str
    session: str
    turn: str
    tool_use_id: str
    cwd: str
    transcript: str
    prompt: str


def normalize(agent: str, p: Dict[str, Any]) -> AgentEvent:
    """Normalize hook payloads from different agents into one shape."""
    if agent == "antigravity":
        event = _s(p.get("hookEventName"))
        tool_call = p.get("toolCall") if isinstance(p.get("toolCall"), dict) else {}
        workspaces = p.get("workspacePaths") if isinstance(p.get("workspacePaths"), list) else []
        cwd = p.get("cwd") or (workspaces[0] if workspaces else "")
        return AgentEvent(
            agent=agent,
            event=event,
            kind=_KIND.get(event, "other"),
            tool=_s(tool_call.get("name")),
            session=_s(p.get("conversationId")),
            turn="",
            tool_use_id=_s(tool_call.get("id")),
            cwd=_s(cwd),
            transcript=_s(p.get("transcriptPath")),
            prompt=_s(p.get("lastUserInput")),
        )

    # Codex, Claude Code, and our OpenCode plugin all use the snake_case schema.
    event = _s(p.get("hook_event_name"))
    return AgentEvent(
        agent=agent,
        event=event,
        kind=_KIND.get(event, "other"),
        tool=_s(p.get("tool_name")),
        session=_s(p.get("session_id")),
        turn=_s(p.get("turn_id") or p.get("prompt_id")),
        tool_use_id=_s(p.get("tool_use_id")),
        cwd=_s(p.get("cwd")),
        transcript=_s(p.get("transcript_path")),
        prompt=_s(p.get("prompt")),
    )


def summarize(e: AgentEvent) -> str:
    """Human summary for a checkpoint created while handling `e`."""
    who = f"{e.agent} · {e.tool}" if e.tool else e.agent
    if e.kind == "post-tool":
        return who
    if e.kind == "pre-tool":
        return f"outside change (seen before {who})"
    if e.kind == "turn-start":
        return f"outside change (before {e.agent} turn)"
    if e.kind == "turn-end":
        return f"{e.agent} · end of turn"
    return f"{e.agent} · {e.event or 'event'}"


def hook_stdout(agent: str) -> str:
    """What an agent expects on stdout from a hook command."""
    return "{}\n" if agent == "antigravity" else ""
