# Complex v3 composition validator

This standalone validator checks a generated + authored sector package without loading production scenes. It never repairs objects or generators.

Input schema `caretaker.composition_validation_input` 1.0.0 contains sector/space bounds, exact anchor IDs and kinds, generated infrastructure AABBs, and authored object AABBs/bindings. The validator checks:

- missing, duplicate and wrong-kind anchors/object IDs;
- sector and space containment;
- positive wall/floor/ceiling penetration;
- door-clearance and required-passage blockage;
- floor support within tolerance;
- wall-mounted footprint against anchor length and height;
- stair and shaft conflicts;
- generated wall/clearance defects, assigned to `responsible_owner=generated` rather than authored content.

Every issue has an object ID, optional anchor ID, deterministic code/ID, measurement with units and relation, proof kind, responsible owner, and allowed actions. Outputs are deterministic:

- `validation_report.json` — complete machine report;
- `repair_queue.json` — `caretaker.repair_queue` 1.0.0, suitable for a repair package;
- `validation_report.md` — short human summary.

Static AABB evidence is `proof_kind=static_geometry`. Optional Godot evidence is loaded only from a matching `caretaker.composition_physics_proof` document and remains `proof_kind=runtime_physics`; the two counts are never conflated.

## Commands

```powershell
python <pipeline>/composition_validator/validate_composition.py `
  --input <pipeline>/composition_validator/fixtures/clean.json `
  --output <staging-report-dir>

godot --headless --path . `
  --script res://<pipeline>/composition_validator/physics_proof_check.gd `
  -- --output <absolute-path>/physics_proof.json

python <pipeline>/composition_validator/validate_composition.py `
  --input <pipeline>/composition_validator/fixtures/physics_input.json `
  --physics-proof <absolute-path>/physics_proof.json `
  --output <staging-report-dir>
```

Exit `0` means no blocking issues. Exit `2` means invalid input or at least one blocking issue. Warnings remain in the report and never change a blocked result to clean.

## Limits

AABB checks are conservative and require canonical world-metre bounds. Non-box meshes, sloped support, and collision-layer behavior require a matching Godot physics proof. Candidate anchors may be reported by later tooling but are never applied by this validator. Sample outputs under `reports/` are fixture evidence, not production audit claims.
