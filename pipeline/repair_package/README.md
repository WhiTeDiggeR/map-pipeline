# Complex v3 repair package builder

`build_repair_package.py` converts a T09 composition report and repair queue into minimal, deterministic context plus a restricted agent prompt. It never invokes an agent and never edits source scenes.

Required inputs are the composition report/input, repair queue, normative bindings, generated anchor frames, one authored sector scene, project root, an empty output directory, and a validation command encoded as a JSON string array. Without `--object-id`, all open blocking object IDs are included; repeated `--object-id` selects a subset and rejects IDs not present in the open blocking queue.

```powershell
python <pipeline>/repair_package/build_repair_package.py `
  --composition-report <validation_report.json> `
  --repair-queue <repair_queue.json> `
  --composition-input <composition_input.json> `
  --bindings <object_bindings.json> `
  --anchor-frames <anchor_frames.json> `
  --authored-scene <sector.tscn> `
  --project-root . `
  --validate-command-json '["python","<pipeline>/composition_validator/validate_composition.py","--input","resolved.json","--output","reports"]' `
  --output <empty-package-directory>
```

The builder requires unique object and binding identities, exactly one binding per selected object, and identical map/sector/generation context. It copies only filtered diagnostics, repairs, objects, bindings, relevant anchors, the authored sector scene, and explicitly allowlisted non-scene files. A second `.tscn` is rejected as a neighboring scene. The package contract marks `Generated/**`, SVG, anchor frames, converter settings, and neighboring sectors read-only/forbidden.

Run tests with:

```powershell
python -m unittest discover -s <pipeline>/repair_package/tests -v
```

## Explicit Agent Fix runner

`run_agent_fix.py` is the only orchestration entry point that invokes an agent,
and it must be started by an explicit editor action. It accepts the latest T24
safe report, rejects clean or non-composition failures, verifies current source
and sector-config hashes plus every evidence artifact, and builds a persistent
minimal package inside that attempt's evidence directory.

```powershell
python <pipeline>/repair_package/run_agent_fix.py `
  --sector U-CONTROL `
  --manifest <project manifest> `
  --safe-report <latest-safe-report.json> `
  --python <python> `
  --svg-tool-root <svg-tool-root> `
  --stair-tool-root <stair-tool-root> `
  --agent-launcher <configured-launcher>
```

The launcher may be one executable path or a JSON string array containing an
executable and fixed arguments. The runner appends `--project-root`,
`--repair-package`, and `--prompt`; a production Codex wrapper must accept those
arguments and return the agent exit code.

The repository provides that wrapper as `codex_agent_launcher.py`. Configure
**Agent launcher** with a JSON string array so Windows paths with spaces remain
unambiguous:

```json
["C:\\Path\\To\\python.exe", "D:\\Path\\To\\project\\tools\\complex_v4_repair_package\\codex_agent_launcher.py", "--codex", "C:\\Path\\To\\codex.exe"]
```

The wrapper runs `codex exec` non-interactively in the project root with the
`workspace-write` sandbox, automatic approval review, and an ephemeral session.
It passes the generated prompt through stdin and points the agent at the
authoritative repair package. It never uses a shell or bypasses Codex sandboxing.
Use `--check` with the same three runner-appended paths to validate a setup
without starting an agent:

```powershell
python <pipeline>/repair_package/codex_agent_launcher.py `
  --codex <codex.exe> `
  --project-root . `
  --repair-package <repair_package.json> `
  --prompt <agent_prompt.md> `
  --check
```

Before and after the launcher, the runner hashes project files and immutable
attempt evidence. Only the current sector authored scene, its bindings, and
explicit `safe_regeneration.agent_allow_files`/`--allow-file` entries may change.
Any other change blocks revalidation. A successful agent exit always triggers a
new full `safe_regenerate.py` run—never `--validate-only` and never validation of
the old live package. The editor may reload only when that run returns a ready
`success` or `noop` report. The final safe report records agent invocation,
command, exit code, changed allowlisted files, forbidden changes, package path,
and the complete revalidation result.

## Claude Code launcher

`claude_agent_launcher.py` is the same wrapper contract for Claude Code. It runs
`claude -p` non-interactively with only `Read,Edit,Write,Glob,Grep` available (no Bash),
`acceptEdits`, no session persistence, the project root as working directory and
`--add-dir`, and the generated prompt on stdin (UTF-8). Edit permission is still limited
by the runner's allowlist check, which rejects any other changed file.

```json
["C:\Path\To\python.exe", "D:\Path\To\project\tools\complex_v4\repair_package\claude_agent_launcher.py", "--claude", "C:\Tools\bin\claude.cmd"]
```

`--claude` may be omitted when `claude` resolves from `PATH`. The Claude Code CLI must be
signed in (`claude` then `/login`) before the first Agent Fix; an unauthenticated CLI exits
non-zero with `Not logged in`, and the runner reports that exit code. Validate the setup
without starting an agent with `--check`, exactly as for the Codex wrapper.
