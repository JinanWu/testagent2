# Hermes-like aiagent approval resume debugging

Use this reference when a web AgentRuntime supports dangerous-tool approvals and the UI has an Approvals panel/button.

## Symptom

- Natural-language approval such as `我批准` may work, but clicking the UI `允許` button appears to do nothing from the user's point of view.
- The approve endpoint may execute the tool and mark the approval `executed`, but the agent does not continue the original task or produce a final assistant answer.
- The frontend may display raw approval/tool JSON instead of a normal assistant response.

## Root cause pattern

A split approval implementation often has two paths:

1. Chat/runtime path: user sends `我批准`, Runtime finds latest pending approval and executes it.
2. API/UI path: frontend calls `POST /api/approvals/{approval_id}/approve`.

If path 2 only calls `執行已批准工具(...)` and returns tool JSON, it bypasses the model/tool loop. The tool ran, but the original user-visible task did not resume.

## Fix pattern

Make the API/UI approval path do three things:

1. Execute the approved tool and persist the tool message/result in the same session.
2. Create a synthetic resume message such as:

   ```text
   approval_id=<id> 已批准且工具 <tool> 已執行完成。
   工具結果如下：
   <tool result>

   請根據這個已完成的工具結果，完成原本使用者請求並給出最終回覆。不要重複呼叫同一個已批准工具，除非工具結果明確失敗且需要低風險查詢來確認狀態。
   ```

3. Re-enter the AgentRuntime with the same session/user context so the model produces the final assistant answer.

Return a response shaped for the frontend, for example:

```json
{
  "approval": {"status": "executed", "result": "..."},
  "session_id": "...",
  "status": "completed",
  "answer": "...",
  "events": [],
  "warnings": [],
  "error": null
}
```

The frontend `允許` handler should display `data.answer` when present, not only stringify the returned JSON.

## Hermes terminal force pitfall

When aiagent has its own approval layer but delegates execution to Hermes `terminal`, Hermes may still apply its internal dangerous-command check. For already-approved terminal calls, pass through the internal force path rather than the model-exposed schema:

- Add `force=True` when calling `tools.terminal_tool.terminal_tool(...)` directly after approval.
- Do not rely on adding `force` to the model/tool schema; Hermes `_handle_terminal` may intentionally not forward it.

## Regression tests

Add an API-level test that creates a pending approval, calls the same `/api/approvals/{id}/approve` endpoint the UI uses, and asserts:

- `response["approval"]["status"] == "executed"`
- tool result is present in `response["approval"]["result"]`
- top-level `response["status"] == "completed"`
- top-level `response["answer"]` is non-empty
- DB approval status is `executed`

For live verification, create a disposable folder, request deletion, click/call approve, and verify both:

- final answer says the folder was deleted
- the path no longer exists
