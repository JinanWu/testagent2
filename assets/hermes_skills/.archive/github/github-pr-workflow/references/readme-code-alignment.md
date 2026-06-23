# README-to-Code Alignment PR Checklist

Use this when the user asks for a README/documentation refresh that should match the current implementation.

## Fast workflow

1. Start from the integration branch the user specified or the repo convention requires.
   - For develop-first repos, fetch, checkout `develop`, pull, then create the docs branch from `develop`.
   - Open the PR with `--base develop`; do not default to `main`.
2. Inspect the README and the implementation files before editing.
   - Entrypoints/routes: `app.py`, `main.py`, server/router files.
   - Deployment: `cloudbuild.yaml`, GitHub Actions, Dockerfile, Procfile, Helm/K8s manifests.
   - Runtime config: env vars in code and deployment config.
   - Data contracts: request decoding, required fields, response shape, Pub/Sub/topic names, queue names.
   - Model/data assets when relevant: `final_models/`, training summaries, config JSON, requirements.
3. Rewrite stale claims instead of making tiny patches around them.
   - Replace old architecture descriptions with the actual live path from code.
   - Document actual route/path, payload envelope, required fields, output fields, and local test commands.
   - Add a short “known注意事項 / known caveats” section for implementation/deployment mismatches found during inspection.
4. Keep the PR docs-only unless the user explicitly asked to fix code.
5. Verify before push.
   - Run `git diff --check`.
   - Confirm `git diff --stat` shows only intended docs files.
   - Commit with the language/style requested by the user.
6. Push and create PR against the correct base branch. Verify PR JSON includes the intended `baseRefName`, `headRefName`, and commit headline.

## Common pitfalls found in README refreshes

- README endpoint examples can drift from actual routes. Confirm decorators/router definitions rather than trusting old docs.
- Input examples often omit fields the code indexes directly; include every required key the code reads.
- Deployment secrets/env var names can drift between YAML and app code. Document the mismatch as a caveat unless asked to fix it.
- Model architecture docs often remain from an older approach. Prefer current runtime imports, model loaders, and training summary files over historical README text.
- For Pub/Sub/queue services, document both the outer transport envelope and the decoded inner payload.
