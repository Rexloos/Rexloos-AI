# Project instructions

## Language

- Write all repository content in English, including code, comments, documentation, names, and user interface text.
- Communicate with the user in the language they use in their messages.

## File changes and Git

- Use `apply_patch` to create and edit files.
- Keep large assets out of Git: Android SDK packages, emulator disks, APKs, screenshots, datasets, and model checkpoints belong in the local cache, outside the repository by default.
- Maintain `.gitignore` entries for generated and large local assets.
- Do not commit or push unless the user explicitly requests it.
- After completing repository changes, automatically include a suggested English commit message in the final response. Inspect all staged, unstaged, and untracked changes first, and base the suggestion on the full uncommitted scope rather than only the latest edit. Do not create a commit unless explicitly requested. If there are no uncommitted changes, omit the suggestion.

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
