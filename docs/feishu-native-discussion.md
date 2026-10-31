# Native Feishu collaboration (Hermes implementation)

## Scope and ownership

This change prepares the Hermes implementation for ordinary native-mention
collaboration. OpenClaw owns its implementation. Model notification delivery is
out of scope. A previously started agent turn keeps its original instructions;
changing source files does not relax instructions of an active conversation.
Cutover requires a controlled restart/new conversation by the rollout owner.

## Contract

- Only configured groups and known peer identities can trigger bot work.
- Native mention is mandatory even when allow_bots is set to all. Visual @ text
  is not a native mention. Self messages and unknown identities stay rejected.
- The transport does not create protocol markers or force a return mention.
  Only an explicit peer @ in output becomes a native mention.
- Preserve reply ancestry in the main chat; root_id is not a thread_id.
- Distinguish sent, received, admitted, running, completed and verified.
- Human task/correction takes precedence. Default roles: OpenClaw summarizes,
  Hermes reviews. Two conversational round trips are the prompt-level default;
  max_peer_turns (default 6 per side/peer) is the runtime safety ceiling.
- The runtime window is turn_window_seconds (default 300). Message IDs and
  normalized content are persisted under HERMES_HOME with a cross-process lock.
  Reaching the budget or deadline never automatically restarts a bot-only loop.
- Exact human stop commands (/stop, 停止讨论, 停止协作, 结束讨论,
  停止机器人讨论) persistently block peer admission. A new admitted human turn
  starts fresh budgets; redelivering the same human message cannot reset them.
- State corruption/I/O failure fails closed for peers, not for humans. No raw
  discussion text or credentials are persisted in the budget file.

## Validation and release

All execution, syntax checks and tests run in GitHub Actions only. The first
commit reproduces the forced-return-mention regression. The following commit
adds persistence/concurrency/admission contracts and the existing Feishu adapter
suite. The workflow only uploads an artifact after both jobs pass.

Deploy only the artifact for the verified commit. Preserve the live baseline
adapter and the previous overlay before copying files; do not run the shared
apply-overlays.sh because it also overwrites OpenClaw and runs host checks.
Copy only a2a.py, adapter.py and discussion.py into Hermes and its overlay.
Do not restart another agent. One rollout owner coordinates drain/restart and
new-conversation acceptance. Cross-agent discussion is not accepted merely
because these unit and adapter tests pass.

Acceptance still requires actual native messages in both directions, a normal
unmentioned conclusion, duplicate rejection, turn/deadline cutoff, human stop,
restart persistence and correct main-chat reply ancestry. Any pending case must
be reported as pending, not inferred from a successful API send.
