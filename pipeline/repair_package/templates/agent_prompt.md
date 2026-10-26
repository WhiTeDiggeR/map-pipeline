# Restricted map repair task

Repair only the listed authored objects in sector `{{SECTOR_ID}}`.

## Objects in scope

{{OBJECT_IDS}}

## Allowed repair actions

{{ALLOWED_ACTIONS}}

Do not infer another action. Missing or ambiguous data is blocking and must be reported.

## Writable allowlist

{{WRITABLE_FILES}}

Only these files may change. Everything else is read-only. In particular, do not modify SVG sources, `Generated`, generated geometry, anchor frames, converter settings, generator settings, or any neighboring sector. Do not silently replace a missing anchor ID.

After the repair, run exactly this validation command (argument array):

```json
{{VALIDATE_COMMAND}}
```

Do not run another agent. Your delivery must contain these headings:

1. `Result`
2. `Changed artifacts`
3. `Validation`
4. `Risks`
5. `Commit`
