# aiagent Gemini ADC project switch

This repo already reads the GCP project from `AIAGENT_GCP_PROJECT` in `後端/服務.py`:
- default in code was `lab-cola-rd`
- runtime model client is `GeminiADC客戶端(設定.gcp專案, 設定.gcp地區, 設定.gemini模型)`
- Gemini is initialized with `genai.Client(vertexai=True, project=專案, location=地區)`

Recommended local launch pattern when the user is authenticated via Google ADC but needs a different Vertex/Gemini project:

1. Start the app with an explicit project override, e.g.
   `AIAGENT_GCP_PROJECT=trade-397602 AIAGENT_MODEL_MODE=gemini uvicorn 後端.服務:應用 --host 127.0.0.1 --port 8000`
2. Verify `/api/health` echoes the selected project and model mode.
3. Verify `/api/chat` with a tiny prompt before doing any deeper UI or SSE testing.
4. If the health endpoint reports the correct project but the chat call fails, inspect the Gemini/Vertex auth path separately from the web stack.

Observed health shape for this repo:
- `project`
- `location`
- `model`
- `mode`
- `tools_count`

Observed minimal chat success shape:
- `status: completed`
- short text answer
- events include `run.started`, `prompt.built`, `model.started`, `message.completed`

Pitfall:
- Do not edit code just to swap projects when the repo already honors an environment variable. Prefer a shell override for the session and keep the source default unchanged unless the project truly needs a new default.