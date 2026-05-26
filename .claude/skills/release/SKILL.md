---
name: release
description: Cut a coordinated release of the voice-channel plugin — bump the version across all manifests, write a CHANGELOG entry, commit, push, tag, and publish a GitHub release. Use this skill whenever the user says "release", "cut a release", "version bump", "ship it", "publish", "do the release", "changelog and push", or finishes a batch of changes and wants to ship them. Optionally takes a bump-level hint (`patch`/`minor`/`major`) or an explicit version.
---
# Release

Cut a release for the voice-channel plugin. This repo ships **two components that version in
lockstep** — the TypeScript/Bun plugin (`plugin/voice/`) and the Python dispatcher
(`dispatcher/`) — joined by the WebSocket protocol in `PROTOCOL.md`. A release bumps a single
shared version across both, writes one CHANGELOG entry at the repo root, commits, pushes, and
tags as `voice--v<X.Y.Z>`.

There is no plugin slug to pick — this repo has exactly one plugin (`voice`). If the user passed a
bump level (`patch`/`minor`/`major`) or an explicit version, use it to seed Step 2.

## Why this skill exists

Version strings live in **five** places that drift out of sync if bumped by hand, and
`claude plugin tag` only checks two of them. The CHANGELOG is the artifact humans read to decide
whether to upgrade and re-pair their agents, so it has to be written deliberately, not
auto-generated. This skill makes the whole sequence repeatable so a release is never half-done
(e.g. tagged but with a stale `pyproject.toml`, or pushed without a CHANGELOG entry).

## The five version locations

Every release updates **all** of these to the same `X.Y.Z`:

| Location | How to read it |
|----------|----------------|
| `plugin/voice/.claude-plugin/plugin.json` | `jq -r '.version'` |
| `plugin/voice/package.json` | `jq -r '.version'` |
| `.claude-plugin/marketplace.json` | `jq -r '.plugins[] \| select(.name=="voice") \| .version'` |
| `dispatcher/pyproject.toml` | `grep -m1 '^version' dispatcher/pyproject.toml` |
| `plugin/voice/server.ts` | the `new Server({ name: 'voice', version: 'X.Y.Z' }, …)` literal |

The first three are JSON; edit the `version` field. `pyproject.toml` is `version = "X.Y.Z"`. In
`server.ts` it's a string literal inside the `Server` constructor — edit it with the Edit tool, not
a blind sed, so you don't touch the protocol version or anything else.

**Do not touch `v` in `PROTOCOL.md`.** That is the *wire* protocol version, bumped only for
breaking message-schema changes — a separate, deliberate decision documented in `CLAUDE.md`. It is
unrelated to the release semver and must never be auto-bumped here.

## Steps

### 1. Pre-release validation

Run these before anything else. Abort the release if any fails — a broken release is worse than a
late one.

1. **Validate the plugin manifest:**
   ```bash
   claude plugin validate plugin/voice 2>&1
   ```
   Must print `Validation passed`.

2. **Run the dispatcher test suite** (Python):
   ```bash
   (cd dispatcher && python -m pytest -q 2>&1)
   ```
   The heavy audio libs are imported lazily, so the suite runs without them. If you see
   collection errors that look like missing audio deps rather than real failures, note it and
   continue; any actual test failure stops the release.

3. **Run the plugin test suite** (Bun/vitest):
   ```bash
   (cd plugin/voice && bun install && bun run test 2>&1)
   ```
   `bun install` first — the WS integration test spawns `bun server.ts`, and a missing dep makes
   it skip silently rather than fail loudly.

   If any test fails, stop and fix before releasing.

### 2. Determine the version bump

**Already-bumped fast-path.** Find the latest release tag and compare it to the manifest:

```bash
git tag --list "voice--v*" | sort -V | tail -1
jq -r '.version' plugin/voice/.claude-plugin/plugin.json
```

If `plugin.json` is already *ahead* of the latest tag (the version files and CHANGELOG were bumped
in an earlier session or on a branch that has since merged), the bump and CHANGELOG are already
done — skip Steps 2–6 and jump to Step 7's commit check (there may be nothing to commit) then
straight to tagging in Steps 8–9.

**Normal path.** Read the current version from `plugin/voice/.claude-plugin/plugin.json` and the
recent entries in `CHANGELOG.md`. Review what changed since the last release:

```bash
git log "$(git tag --list 'voice--v*' | sort -V | tail -1)"..HEAD --oneline
git diff "$(git tag --list 'voice--v*' | sort -V | tail -1)"..HEAD --stat
```

Decide the bump level:
- **Patch** (0.0.X) — bug fixes, doc updates, small behavioral tweaks.
- **Minor** (0.X.0) — new features, new skills, config-schema changes, anything that makes operators re-pair or reconfigure.
- **Major** (X.0.0) — only if the user explicitly asks.

A security-relevant change to the wire/pairing handshake (like the 0.0.2 cert-pinning work) that
forces operators to re-pair is at least a **minor** even if it looks small in the diff — the
upgrade isn't free for the operator.

Present the suggested version with a one-line rationale and **wait for confirmation** before
editing anything.

### 3. Write the CHANGELOG entry

Prepend a new entry to `CHANGELOG.md`, immediately after the `# Changelog` header and before the
previous version. Match the existing house format:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added / Changed / Fixed
(use only the sections that apply)

- **component: one-line summary** — short rationale if it isn't obvious from the summary.

### Upgrade Instructions

Prose, human-facing. What does an operator actually have to DO to adopt this release? If nothing
beyond updating the plugin, say so explicitly.
```

Conventions, learned from the existing entries:

- **Narrative bullets** lead with the component (`dispatcher:`, `plugin:`, `security:`), one line
  each, ~40 words max. Describe the change and *why*, not the file-by-file diff. Don't enumerate
  internal refactors, helper extractions, or test scaffolding — those live in `git diff`.
- **Upgrade Instructions** is prose for a human, not a script for another tool. If the release
  changes the pairing handshake, config schema, or anything stored in the operator's state dir,
  spell out the re-pair / reconfigure steps (e.g. *"re-pair every agent with a fresh
  `voicepair_…` string from `voice-dispatcher config rotate-token <id>`"*). Both the dispatcher
  and the plugin usually need upgrading together — say so.
- **Files affected table** (the `| File | Change |` table) is optional. Use it for large or
  initial releases where a reader benefits from the map; skip it for focused changes.

### 4. Refresh narrative references (only if the surface changed)

If this release **added or renamed** a skill, command, config field, or protocol message, update
the prose that documents it — these don't auto-update and go stale silently:

- `README.md` — feature/config tables, the pairing/TLS section, the URL table.
- `plugin/voice/README.md` — skills table, config example block.
- `PROTOCOL.md` — **only** if a message shape changed. Adding an *optional* field is non-breaking
  (document it, leave `v` alone); a new required message or removed field is breaking (that's the
  deliberate `v` bump, which the user must explicitly request — it is not part of this skill).
- `plugin/voice/skills/configure/SKILL.md` and `.../status/SKILL.md` — if the config keys or
  pairing flow changed.

Skip this step entirely if the release added nothing new to document. Pure bug-fix releases
usually touch nothing here.

### 5. Bump the version in all five locations

Edit each of the five locations from the table above to the new `X.Y.Z`. Use the Edit tool for
each (especially `server.ts`, where a sed could clobber the wrong literal).

Then confirm all five agree:

```bash
NEW=X.Y.Z   # the version you just set
jq -r '.version' plugin/voice/.claude-plugin/plugin.json
jq -r '.version' plugin/voice/package.json
jq -r '.plugins[] | select(.name=="voice") | .version' .claude-plugin/marketplace.json
grep -m1 '^version' dispatcher/pyproject.toml
grep -m1 "name: 'voice', version:" plugin/voice/server.ts
```

All five must show `$NEW`. `claude plugin tag` (Step 9) only checks plugin.json ↔ marketplace, so
the other three are on you to verify here.

### 6. Final validation

Steps 3–5 only edit Markdown, JSON, TOML, and one TS literal, so the test suites don't need a
re-run. Confirm the manifests still parse and the working tree contains only release files:

```bash
jq -e . plugin/voice/.claude-plugin/plugin.json > /dev/null
jq -e . plugin/voice/package.json > /dev/null
jq -e . .claude-plugin/marketplace.json > /dev/null
git status --short
```

`git status` should show only the files this release touched: `CHANGELOG.md`, the five version
locations, and any reference docs from Step 4. Any unexpected entry → investigate before
committing.

### 7. Commit and push

Stage only the release files by name — never `git add -A`. Commit with the house format (matches
`voice v0.0.1: Initial release`, `voice v0.0.2: in-band cert pinning`):

```
voice v<X.Y.Z>: one-line summary of the release
```

Then push to `origin`. (Pushing the branch is fine here — the user invoked a release, which is an
explicit publish action. The *tag* still waits for the branch check below.)

### 8. Branch check before tagging

```bash
git branch --show-current
```

- **On `main`** → tag immediately (Step 9).
- **On any other branch** → **stop.** Tagging a branch tip creates a commit SHA that `main` won't
  carry after a squash/rebase merge, stranding the tag on an orphan commit. Recommended path: open
  a PR, merge to `main`, then re-run `/release` from `main` — Step 2's already-bumped fast-path
  will skip straight here. Offer tagging-now only if the user explicitly accepts the risk, and
  wait for their choice.

### 9. Tag and publish

The tag format is `voice--v<X.Y.Z>` (double-dash) — what `claude plugin tag` produces and what the
existing `voice--v0.0.1` tag uses. The command validates that `plugin.json` and the marketplace
entry agree, requires a clean working tree, and refuses to clobber an existing tag:

```bash
claude plugin tag plugin/voice --push
```

If `claude plugin tag` is unavailable, fall back to plain git (same tag name):
```bash
VERSION=$(jq -r '.version' plugin/voice/.claude-plugin/plugin.json)
git tag -a "voice--v$VERSION" -m "voice v$VERSION"
git push origin "voice--v$VERSION"
```

Then create the GitHub release, sourcing notes from the CHANGELOG section just written (don't use
`--generate-notes` — it would re-list raw commits instead of the curated entry):

```bash
VERSION=$(jq -r '.version' plugin/voice/.claude-plugin/plugin.json)
TAG="voice--v$VERSION"
NOTES_FILE=$(mktemp)
awk -v ver="$VERSION" '
  $0 ~ "^## \\[" ver "\\]" {flag=1; next}
  /^## \[/ && flag {exit}
  flag {print}
' CHANGELOG.md > "$NOTES_FILE"
[ ! -s "$NOTES_FILE" ] && { echo "CHANGELOG section for $VERSION not found — fix and retry"; rm "$NOTES_FILE"; exit 1; }
gh release create "$TAG" --title "$TAG" --notes-file "$NOTES_FILE"
rm "$NOTES_FILE"
```

### 10. Report

Print the new version, the commit hash, the tag name, the GitHub release URL, and a one-liner
confirming it's pushed.

## Don't

- Don't `git add -A` — stage release files by name so stray working-tree changes don't ride along.
- Don't bump `v` in `PROTOCOL.md` as part of a release.
- Don't tag off a non-`main` branch without the user's explicit go-ahead.
- Don't skip the test suites or the five-location version check — a tagged release with a stale version file is the exact failure this skill exists to prevent.
- Don't push or tag before the user has confirmed the version bump in Step 2.
