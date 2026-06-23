# Hermes-like aiagent approval + checkpoint parity

Use this when building a Hermes-like FastAPI/SSE agent runtime that exposes file, terminal, and process tools.

## Durable implementation pattern

1. Treat dangerous tool execution as a runtime state, not just a frontend confirmation.
   - Add an `approval_requests` table with `id`, `run_id`, `session_id`, `tool_call_id`, `tool_name`, `arguments_json`, `reason`, `status`, `result`, `created_at`, `decided_at`, `decided_by`.
   - Runtime emits `approval.requested`, marks the run `waiting_approval`, persists events, and returns `status='waiting_approval'` before executing the tool.
   - Add `GET /api/approvals`, `POST /api/approvals/{id}/approve`, and `POST /api/approvals/{id}/deny`.
   - Add `/approve` and `/deny` slash commands that default to the latest pending approval for the current session.

2. Protect at least these first:
   - `patch`
   - `write_file`
   - `process` with `action='kill'`
   - `terminal` commands matching destructive patterns such as `rm -rf`, `git reset --hard`, `git clean -f`, `shutdown`, `reboot`, `mkfs`, `dd if=`, broad `chmod -R 777`, or writes into `/etc`.

3. Add checkpointing before approved write tools execute.
   - Add `checkpoints` and `checkpoint_files` tables.
   - Store path, existed flag, UTF-8 content, sha256, and timestamp.
   - For files that did not exist at snapshot time, rollback should delete the newly-created file if present.
   - Emit `checkpoint.created` before running the approved write.
   - Expose `GET /api/checkpoints`, `POST /api/checkpoints`, and `POST /api/checkpoints/{id}/rollback`.

4. Keep the first version honest about scope.
   - A minimal approval implementation may execute the approved tool and complete the original run without resuming the model loop.
   - Label that as a first version; the later Hermes-like upgrade is to resume the original model/tool loop and let the model summarize the approved tool result.

## Frontend contract

- Add an Approvals tab or panel that lists pending approvals with tool name, reason, arguments, and Approve/Deny buttons.
- Subscribe to `approval.requested`, `run.waiting_approval`, `approval.executed`, and `approval.denied` SSE events.
- Treat `message.completed` with `status='waiting_approval'` as a paused run, not a failed run.

## Verification checklist

- Regression test: dangerous `write_file` creates one pending approval and does not call the handler before approval.
- Regression test: approve executes the original tool and records `executed`.
- Regression test: deny records `denied` and does not execute the tool.
- Regression test: approved `write_file` creates a checkpoint; rollback restores the original content.
- API test: list approvals and approve a safe fake tool call.
- API test: create checkpoint, mutate temp file, rollback, and assert content restored.
- Run `py_compile`, full pytest, and a docstring/custom-definition naming check when the project intentionally uses Traditional Chinese Python identifiers.
