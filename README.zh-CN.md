# undokey

**AI 编程 Agent 的通用撤销键。**

把 Agent 的每一步工具调用都存成可随时回退的文件快照——支持 **Codex CLI**、**OpenCode**、**Claude Code**、**Antigravity**，**完全不碰你项目自己的 `.git`**。

对 **Codex**，`undokey rewind <轮次> --chat` 可以**把文件和对话一起回退**（将对话截断后另存为新会话，原会话不动）。

[English README](./README.md)

---

```text
$ undokey log
── 2026-11-21
● 9a098637  16:56:47  codex · Bash                                      1 file +0 -1
  36482239  16:56:47  outside change (before codex turn)                1 file +1 -0
  52e5ce27  16:56:47  codex · apply_patch                               2 files +2 -0
  d306f314  16:56:47  baseline (first checkpoint)                       1 file +1 -0

$ undokey turns
#1   codex       16:56:47  1 tools   重构鉴权中间件
#2   codex       16:58:12  3 tools   重写数据库层

$ undokey rewind 2 --chat
✓ files now match 52e5ce27 (before turn #2)
  changed your mind? undokey to 9a098637
✓ conversation forked to before turn #2 (original session untouched)
  continue with: codex resume 01a1fd49-cc5b-78d7-a474-d3860f5acdd3
```

---

## 为什么做这个

- **Codex 官方去掉了 `/undo`**，而“同时回退代码和对话”一直是仓库里点赞最高的未解决需求之一。
- **平时会在多个 Agent 之间切换**（Codex、Claude Code、OpenCode、Antigravity），各家的回退能力参差不齐。
- **单靠 `git checkout` 不够用**：Agent 常在未提交的代码上继续改、创建新文件、删掉未跟踪文件，或者执行破坏性命令。

`undokey` 挂在各家 Agent 的原生生命周期 Hook 上，把快照存到 `~/.undokey/` 下的独立影子 git 仓库里：

- 项目的 `.git`、暂存区、提交历史和 stash **完全不受影响**。
- 每次回退前都会自动存一份当前快照——**撤销本身也能撤销**。
- 纯 Python 标准库实现，**零第三方依赖**（仅需 Python ≥ 3.9 和 `git`）。

## 安装

使用 [uv](https://docs.astral.sh/uv/)（推荐）：

```bash
uv tool install git+https://github.com/Simon-rss/undokey.git
```

使用 `pipx` 或 `pip`：

```bash
pipx install git+https://github.com/Simon-rss/undokey.git
# 或
pip install git+https://github.com/Simon-rss/undokey.git
```

或者本地克隆安装：

```bash
git clone https://github.com/Simon-rss/undokey.git
cd undokey && pip install -e .
```

## 快速开始

```bash
# 1. 自动检测本机已安装的 Agent 并写入 Hook（修改前会自动备份原配置）
undokey init

# 2. 在任意 git 项目里照常使用你的 Agent
#    （Codex 首次需要进入 codex 运行 /hooks 信任 undokey 条目）

# 3. 随时查看和回退
undokey log                 # 查看快照时间线
undokey diff <id>           # 看某一步改了什么（加 --now 对比当前文件）
undokey undo                # 退回上一步（或 undokey undo 3）
undokey to <id>             # 跳到任意一个快照
undokey turns               # 按 Agent 对话轮次查看
undokey rewind 2 --chat     # 文件回到第 2 轮之前，并分叉 Codex 对话
```

只想在当前项目启用、不想改全局配置？

```bash
undokey init --project
```

会自动写入项目本地的 hook 配置，并把它们加入 `.git/info/exclude`，不会污染 git 状态。

## 支持情况

| Agent | 接入方式 | 文件快照与回退 | 对话同步回退（`--chat`） | 验证状态 |
| --- | --- | :---: | :---: | --- |
| **Codex CLI** | `~/.codex/hooks.json` | ✅ | ✅（分叉会话） | 实机实测 |
| **OpenCode** | `~/.config/opencode/plugin/undokey.js` | ✅ | — | 实机实测 |
| **Claude Code** | `~/.claude/settings.json` | ✅ | 可直接用内置 `/rewind` | Hook 格式已测 |
| **Antigravity** | `~/.gemini/config/hooks.json` | ✅ | — | 按官方协议已测 |

> **关于 Codex `--chat`**：Codex 会话文件属于内部格式。`undokey` 不会修改原会话，而是另存一份截断后的新会话文件，用 `codex resume <新ID>` 接着聊即可。即使未来 Codex 改动内部格式，文件快照和 `undokey rewind <轮次>` 依然正常使用。

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `undokey init [--agent 列表] [--project]` | 安装 Hook（默认自动检测本机 Agent，全局生效） |
| `undokey uninstall [--agent 列表] [--project]` | 卸载 Hook（保留已记录的快照） |
| `undokey status` | 查看安装状态与当前项目快照数 |
| `undokey log [-n 30]` | 查看快照时间线 |
| `undokey diff <id> [--now] [--stat]` | 查看某一步改动（`--now` 为对比当前文件） |
| `undokey undo [n]` | 撤销最近 `n` 步（可随时恢复） |
| `undokey to <id>` | 恢复文件到指定快照（可随时恢复） |
| `undokey turns` | 查看对话轮次列表 |
| `undokey rewind <轮次> [--after] [--chat]` | 回退到某轮之前（或 `--after` 之后）；`--chat` 同步分叉 Codex 对话 |
| `undokey snapshot [目录] [--label 说明]` | 手动打一个快照 |
| `undokey track [目录]` / `untrack [目录]` | 记录非 git 目录 |

## 实测解决的关键边界问题

- **并发工具调用**：Agent 同时发起多个工具调用时会并发触发 Hook。影子仓库通过目录锁串行化写入（并带过期锁回收），不会丢快照或报锁冲突。
- **被忽略但已提交的文件**：在 `.gitignore` 里但被 `git add -f` 进仓库的文件会被一并纳入快照，回退不会误删。
- **构建产物与依赖目录**：`node_modules/`、构建目录以及 `.git/info/exclude` 规则内的文件不会被快照，回退时也不会被清掉。
- **忽略 Agent 内部目录**：自动过滤 `~/.codex/memories`、`~/.claude`、`~/.gemini` 等目录，避免后台任务把隐私数据拍进快照。
- **大仓库首次快照不卡顿**：跟踪文件数超过 3,000 时，首次基线快照放到后台执行；后续增量快照在 205 MB / 1 万文件的仓库上实测约 60–190 ms。
- **绝不打断 Agent**：Hook 进程始终返回退出码 `0`，异常仅写入 `~/.undokey/errors.log`。

## 开发与测试

```bash
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
```

## 协议

[MIT](./LICENSE)
