# Reviewer handoff: Asset promotion

Asset repo owns raw art; App repo receives optimized PNGs via a separate PR. No direct App main writes or automatic merges. This implementation PR does not promote any artwork.

## Setup
After review and owner merge, create a fine-grained PAT restricted to wrightc214/NCAA180-Sleeper with Contents and Pull requests read/write; store as APP_PROMOTION_TOKEN in Asset repo Actions secrets. Do not paste token into chat or code. Preview needs no token. Manual workflow runs on main only. Select one PNG or nonrecursive folder under logos/. Publish defaults false. Review preview log, then rerun with publish true; review resulting App PR before merge.

## Design
Uses existing logo_trim.web_copy and png_bytes (4% margin, 512px cap, Pillow 12.3.0). If selected image matches an existing master derivative, render from master; otherwise use selected image. No writes to Asset checkout. Excludes source, retired, Logo Jackpot, glow-dark, throwbacks. No deletions. Unique App branch per run. Manifest records source path, output hash, add/replace, and Asset commit. No glow generation here; review existing App post-merge trim/glow workflow interaction, especially old masters and unintended follow-up commits.

## Review and validation
- Run python -m pytest tests with pinned Pillow; tests not executed by author.
- Inspect path containment, symlinks, excluded folders, credential scope and shell quoting.
- Add coverage for replacement, master selection, unchanged existing production copies, invalid PNG, linked paths and no deletion; current tests cover basic rendering, source preservation, repeat run and rejected selections.
- Check whether PNG re-encoding causes needless changes; consider pixel-equivalence policy.
- Check preview/publish drift (separate runs may use different source/App commits); consider explicit commit pins or preview artifact approval before production use.
- Confirm existing App trim/glow automation does not retire or overwrite masters unexpectedly after promotion; resolve before first production merge.
- Verify a preview with a small folder and compare original Asset hashes and App diff; then with owner approval publish a test PR, inspect manifest and images, do not merge automatically.
- Verify App branch protections and failure handling (push may succeed before PR creation; clean orphan branch manually if needed).
- Scope is PNG logos only, no non-logo artwork or recursive folder promotion.
