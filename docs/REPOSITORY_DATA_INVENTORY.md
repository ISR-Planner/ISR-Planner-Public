# Repository Data Inventory

Reviewed on branch `Refactor-2` at commit `c2e1e33`. The working tree was clean
before review. The audit covered 580 tracked files, approximately 8.3 MB, and
found one non-empty exact duplicate pair. This inventory classifies that pair,
all tracked ignore-rule matches, all backup candidates, and the two large NOAA
captures. It is not a declaration that every other repository file is necessary.

## Retain active or referenced files

| Candidate | Decision and evidence |
| --- | --- |
| `.specify/extensions/git/config-template.yml` and `git-config.yml` | Keep both. Their bytes match, but one is a template and one is active configuration. Extension scripts and commands explicitly reference `git-config.yml`. |
| `webapp/editor/tmp_environment.json` | Keep as a referenced migration fixture. `scripts/verify_phase2_latlng.py` loads this exact path; `scripts/migrate_env_to_latlng.py` lists it as a default input. Its filename also matches a runtime-output convention, so the ignore-rule match is not evidence of redundancy. |
| `webapp/editor/mission_control_drone1.json` | Keep as a migration fixture. It is a default input in `scripts/migrate_env_to_latlng.py`. |
| `webapp/_weather_diagnostics/noaa_20261012T012337Z.json` | Keep at its existing path. Diagnostic scripts, demo packaging, and multiple JavaScript tests load it; the diagnostic page also fetches it through a static URL. |
| `webapp/weather_preview_noaa.json` | Keep as a legacy fixture. `tests/e2e/test_weather_geometry.js` reads it explicitly. It is not byte-identical to the other NOAA capture. |

## Retain historical records and originals

| Candidate | Decision and evidence |
| --- | --- |
| Eight `scripts/comparison_result_*.json` files | Keep intentionally retained historical output. `specs/004-agent-benchmark-harness/spec.md` and `docs/REFACTOR_COMPLETION_PLAN.md` explicitly preserve existing results; `.gitignore` excludes future output. |
| `docs/session-notes/README_ANALYSIS.md` | Keep historical documentation. The session-notes index links to it. Add a narrow exception to the broad `*_analysis.md` ignore rule so the audit no longer misclassifies this document. |
| `webapp/editor/mission_control_drone1.json.bak` and `tmp_environment.json.bak` | Keep migration originals alongside their converted fixtures. `docs/LATLNG_MIGRATION_PLAN.md` documents this arrangement. The migration script checks for adjacent backups before creating them; moving them would change that behavior. |
| `docs/session-notes/restore_2026-09-23_before_unified_ops/untracked/docs/architecture/UNIFIED_MISSION_OPERATIONS.md` | Keep as an already separated historical proposal. It differs from `docs/architecture/UNIFIED_MISSION_OPERATIONS.md`. Session handoffs reference the restore directory; this nested copy must not be mistaken for the current canonical architecture document. |

The eight retained comparison results have suffixes `1776794856`, `1776795327`,
`1776795556`, `1776796027`, `1776796426`, `1776796618`, `1776797025`, and
`1776797272`. This decision covers existing tracked records, not future outputs.

## Retain pending a separate legacy data decision

`webapp/editor/ai_solution.json` appears to be a legacy generated result.
`webapp/editor/main.py` reads and removes a file named `ai_solution.json` relative
to its working directory. That does not establish that this committed copy is
needed, but neither does it establish that removing it preserves every legacy
launch workflow. Retain it for now; reassess when the supported legacy-editor
launch and sample-data policy are documented. It is not an exact duplicate of
another tracked file.

## Reference checks before moves

Reference review at `c2e1e33` covered tracked scripts, tests, documentation, HTML,
JavaScript, hidden extension configuration, and deployment files using `git grep`
and targeted source inspection. Searches included basenames and directory names,
not just exact full paths. The following requirements apply if a move is proposed;
no move is approved by an absence of literal matches.

| Candidate | References and requirements for a move |
| --- | --- |
| Git configuration and template | `.specify/extensions/git/extension.yml` associates the template with `git-config.yml`. Bash and PowerShell initialization/commit scripts construct the active config path, and extension commands document it. Changing location requires all those consumers and installation behavior to be reviewed. Keep both paths. |
| Converted editor fixtures and adjacent `.bak` originals | `scripts/migrate_env_to_latlng.py:148` constructs backup paths with `with_suffix(path.suffix + ".bak")`; lines 158–159 list default fixture inputs. `scripts/verify_phase2_latlng.py:26` reads the temporary-environment fixture. `docs/LATLNG_MIGRATION_PLAN.md` documents adjacent originals and restoration. Moving a backup alone changes the migration script's backup-existence check. Keep the group in place. |
| Legacy `ai_solution.json` | `webapp/editor/main.py:1042` reads a working-directory-relative filename and later removes it. `server/api/environment_service.py:19` constructs a separate result path from its injected root. Identical basenames do not prove these are the same file. Establish supported launch directories and data ownership before moving either. |
| Dated NOAA capture | `work/diagnostics/package_weather_demo.py:16`, `work/diagnostics/weather_checkpoint_integration.js:6`, and four JavaScript tests load the current path. Tests are `test_weather_accept.js`, `test_weather_diagnostic_replay.js`, `test_weather_geometry.js`, `test_weather_demo_paths.js`, alongside the integration script outside `tests/`. `webapp/_weather_diagnostics/runner.js:15` fetches the static URL; the replay test checks that URL explicitly. Update filesystem consumers, browser fetches, URL expectations, and weather documentation together if relocating. |
| Legacy NOAA capture | `tests/e2e/test_weather_geometry.js:93` reads `webapp/weather_preview_noaa.json`. It also has a static URL through the directory mount. Keep as a referenced fixture. |
| Historical comparison results | `scripts/test_agent_compare.py:110` constructs new output filenames with a timestamp. `specs/004-agent-benchmark-harness/spec.md:137` and `docs/REFACTOR_COMPLETION_PLAN.md:349` retain existing output in place. No direct reader for the eight individual timestamped names was found in the searched source; this is not proof that external consumers do not exist. Preserve the retention policy. |
| Historical analysis document | `docs/session-notes/README.md:14` links to the current file. Cleanup specifications record its former root location; the document itself contains an old absolute self-reference. If relocated, update the live index and add a location note without rewriting historical path evidence. |
| Restore proposal copy | `docs/session-notes/HANDOFF_2026-09-23_unified_ops.md:11` and `HANDOFF_2026-10-10_weather.md:119` reference its restore directory. The canonical document remains under `docs/architecture/`. A relocation would need a historical location note and archive index, not replacement of the canonical document. Keep this historical copy where it is for now. |

### Static URL verification

`server/main.py:103` mounts the entire `webapp` directory at `/static`, rather
than maintaining a list of individual public assets. An isolated local test of
the same Starlette `StaticFiles` handler returned HTTP 200 for HEAD resolution of:

- `/static/editor/tmp_environment.json`
- `/static/editor/tmp_environment.json.bak`
- `/static/editor/mission_control_drone1.json`
- `/static/editor/mission_control_drone1.json.bak`
- `/static/editor/ai_solution.json`
- `/static/weather_preview_noaa.json`
- `/static/_weather_diagnostics/noaa_20261012T012337Z.json`

This verifies local path exposure without starting application workflows. It
does not verify the live Railway deployment, external bookmarks, or access logs.
`Dockerfile.railway` copies `webapp/` into the image subject to `.dockerignore`;
deployment inclusion must be checked separately before promising URL compatibility.
Moving a file outside `webapp/` also changes its static-serving relationship.

No explicit URL references for the editor fixtures were found in the searched
source. They still resolve under the directory mount. Any future relocation must
make an explicit decision about preserving or retiring those URLs, then verify
the relevant build and static response behavior.

### Review limitations

This check covers the current candidate set, not every possible repository move.
It does not discover external consumers, inspect references embedded in binary
documents, or establish the behavior of every working-directory-dependent legacy
launch. Repeat it against the actual proposed source and destination paths before
each move. No application workflows were executed and the existing test blockers
were not changed.

## Changes and recovery

No candidate qualifies for deletion as a confirmed redundancy in this pass.
No data or historical documents were moved. Classification provides the separation
between active fixtures and historical material while preserving referenced paths.

The only ignore-rule change is the specific exception for retained documentation.
Future generated analysis files remain ignored. Tracked ignore-rule matches
should fall from eleven to ten; remaining matches are deliberately visible for
review rather than hidden by broad new exceptions.

All reviewed candidate versions are recoverable from commit `c2e1e33`. Before any
future removal, verify that commit remains available and record the exact original
path. Untracked/private files and stashes are outside this inventory and must not
be assumed recoverable from Git.

## Next review

- Resolve the legacy-editor sample-data policy before moving or untracking its
  result files.
- Revisit historical result storage only through an explicit replacement of the
  documented retention policy.
- Use [the refactor checklist](REPOSITORY_INFRASTRUCTURE_REFACTOR_CHECKLIST.md)
  to track broader infrastructure work. Existing test blockers remain recorded in
  [Baseline Blockers](Baseline-Blockers.md).
