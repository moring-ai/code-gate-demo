# Instructions for AI coding agents

This repository enforces AI disclosure in CI (`.github/workflows/ai-code-gate.yml`).
Follow these rules for every change you make.

1. **Mark generated code.** Wrap code you write in markers, using the comment syntax of the file:

   ```python
   # @ai-generated begin tool=<your-tool> model=<model-id> reviewed-by=@<human-github-handle>
   ...code...
   # @ai-generated end
   ```

   For a file you generated entirely, put one header line at the top instead:
   `# @ai-generated file tool=<your-tool> model=<model-id> reviewed-by=@<human-github-handle>`.
   Ask the human for their GitHub handle if you don't know it. Never invent a reviewer.

2. **Add a commit trailer** to every commit that contains AI-generated code:
   `Assisted-by: <tool>:<model-id>` (for example `Assisted-by: claude-code:claude-sonnet-5`).
   Never add `Signed-off-by` on behalf of the human.

3. **Remind the human** to tick both AI disclosure boxes in the PR description.

4. **Dependencies:** do not add a package you have not confirmed exists in the official registry.
