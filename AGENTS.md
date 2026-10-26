# Project instructions

## Language

- Write all repository content in English, including code, comments, documentation, names, and user interface text.
- Communicate with the user in the language they use in their messages.

## Session handoff

- At the beginning of each new session, check for `resume.md` in the repository root. If present, read it fully before starting work and retain its context for the session, then delete it using `apply_patch`. It is a one-time handoff, not permanent documentation. Include the deletion in the next automatic local commit; do not recreate it unless the user requests another handoff.
- When creating a handoff at the user's request, leave the new `resume.md` for the next session to consume. Do not delete it in the session that created it. If it is absent, continue normally.

## File changes and Git

- Use `apply_patch` to create and edit files.
- Keep large assets out of Git: Android SDK packages, emulator disks, APKs, screenshots, datasets, and model checkpoints belong in the local cache, outside the repository by default.
- Maintain `.gitignore` entries for generated and large local assets.
- After every completed user prompt, run `bash scripts/auto_commit.sh "<English commit title>"` from the repository. The user authorizes these local commits automatically; do not ask for confirmation. A later user instruction to avoid committing takes precedence.
- Before running the script, inspect all staged, unstaged, and untracked changes, run the relevant checks, and choose a concise English commit title covering the full uncommitted scope, not only the latest edit. Keep generated/large local assets out of the commit. Do not commit unfinished work or changes with unresolved check failures; report why the commit was not created.
- The script stages repository changes and creates a local commit with the proposed title. If there are no changes, it exits successfully without creating an empty commit. Use a neutral title such as `chore: update project` for the no-change invocation. Report the actual commit title and short hash in the final response when a commit is created.
- Never push automatically. This workflow authorizes local commits only; pushing requires a separate explicit user request. If committing fails, report the failure and preserve the changes; do not reset or discard them.

## Product direction

- Target the real Brawl Stars game running in an Android emulator managed by this project's Python tools.
- Initialize the AI without pretrained weights or demonstrations. Support human guidance through rewards and object annotations.
- Keep learning independent for each brawler and game mode combination. When switching profiles, safely finish pending work and preserve existing models and training data.
- Maintain automatic training recommendations and explain their reasons. Do not treat step counts as proof of mastery.
- Verify the brawler roster against current sources before updating it. Preserve support for custom brawler and mode names.

## Verification and documentation

- Clearly distinguish implemented functionality, automated test results, manual verification in the actual game, and features that remain unimplemented.
- Do not claim that a feature has been verified in the game without actually checking it there.
- Run checks relevant to each change. Update documentation when controls, workflows, or startup instructions change.
