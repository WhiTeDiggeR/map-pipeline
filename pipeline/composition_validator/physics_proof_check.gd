extends SceneTree

const SCHEMA_ID := "caretaker.composition_physics_proof"


func _initialize() -> void:
	var output_path := _read_output_path()
	if output_path.is_empty():
		push_error("usage: -- --output <absolute-json-path>")
		quit(2)
		return
	var world := Node3D.new()
	root.add_child(world)
	_add_static_box(world, "GeneratedFloor", Vector3(10.0, 0.2, 10.0), Vector3(0.0, -0.1, 0.0), "floor")
	_add_static_box(world, "GeneratedWall", Vector3(0.4, 2.0, 2.0), Vector3(0.0, 1.0, 0.0), "wall")
	await physics_frame
	await physics_frame
	var space := world.get_world_3d().direct_space_state
	var object_shape := BoxShape3D.new()
	object_shape.size = Vector3.ONE
	var shape_query := PhysicsShapeQueryParameters3D.new()
	shape_query.shape = object_shape
	shape_query.transform = Transform3D(Basis.IDENTITY, Vector3(0.0, 0.5, 0.0))
	shape_query.collision_mask = 1
	shape_query.collide_with_bodies = true
	var overlaps := space.intersect_shape(shape_query, 16)
	var wall_hits := 0
	for hit: Dictionary in overlaps:
		var collider := hit.get("collider") as CollisionObject3D
		if collider != null and str(collider.get_meta("fixture_kind", "")) == "wall":
			wall_hits += 1
	var floating_support := _ray_has_floor(space, Vector3(3.0, 1.0, 0.0), 0.1)
	var supported_support := _ray_has_floor(space, Vector3(-3.0, 0.05, 0.0), 0.1)
	var checks: Array[Dictionary] = [
		{
			"object_id": "OBJ-PHYS-WALL", "anchor_id": null, "code": "wall_penetration",
			"subject_owner": "authored", "responsible_owner": "authored_content",
			"result": "blocking" if wall_hits > 0 else "clean",
			"measurement": {"actual": wall_hits, "limit": 0, "units": "physics_shape_hits", "relation": "lte"},
			"allowed_actions": ["move_object", "resize_object"],
			"message": "Physics intersect_shape found the authored box overlapping a generated wall body."
		},
		{
			"object_id": "OBJ-PHYS-FLOAT", "anchor_id": null, "code": "support_missing",
			"subject_owner": "authored", "responsible_owner": "authored_content",
			"result": "clean" if floating_support else "blocking",
			"measurement": {"actual": floating_support, "limit": true, "units": "physics_raycast", "relation": "must_equal"},
			"allowed_actions": ["move_object_to_support", "add_generated_support"],
			"message": "Short downward physics ray did not find generated floor support."
		},
		{
			"object_id": "OBJ-PHYS-SUPPORTED", "anchor_id": null, "code": "support_missing",
			"subject_owner": "authored", "responsible_owner": "authored_content",
			"result": "clean" if supported_support else "blocking",
			"measurement": {"actual": supported_support, "limit": true, "units": "physics_raycast", "relation": "must_equal"},
			"allowed_actions": ["review_physics"],
			"message": "Short downward physics ray found generated floor support."
		}
	]
	var document := {
		"schema_id": SCHEMA_ID,
		"schema_version": "1.0.0",
		"map_id": "fixture-map",
		"sector_id": "PHYSICS",
		"generation_id": "sha256:physics",
		"engine": {"name": "Godot", "version": Engine.get_version_info()["string"], "physics": "Jolt Physics"},
		"checks": checks,
	}
	var absolute_path := ProjectSettings.globalize_path(output_path) if output_path.begins_with("res://") or output_path.begins_with("user://") else output_path
	DirAccess.make_dir_recursive_absolute(absolute_path.get_base_dir())
	var file := FileAccess.open(absolute_path, FileAccess.WRITE)
	if file == null:
		push_error("cannot write physics proof: %s" % absolute_path)
		quit(2)
		return
	file.store_string(JSON.stringify(document, "  ") + "\n")
	file.close()
	if wall_hits == 1 and not floating_support and supported_support:
		print("COMPLEX_V4_COMPOSITION_PHYSICS_OK wall_hits=1 floating_support=false supported_support=true")
		quit(0)
	else:
		push_error("unexpected physics fixture result")
		quit(1)


func _add_static_box(parent: Node3D, node_name: String, size: Vector3, position: Vector3, fixture_kind: String) -> void:
	var body := StaticBody3D.new()
	body.name = node_name
	body.collision_layer = 1
	body.collision_mask = 0
	body.position = position
	body.set_meta("fixture_kind", fixture_kind)
	var shape_node := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	shape_node.shape = shape
	body.add_child(shape_node)
	parent.add_child(body)


func _ray_has_floor(space: PhysicsDirectSpaceState3D, origin: Vector3, distance_m: float) -> bool:
	var query := PhysicsRayQueryParameters3D.create(origin, origin + Vector3.DOWN * distance_m, 1)
	query.collide_with_bodies = true
	var hit := space.intersect_ray(query)
	if hit.is_empty():
		return false
	var collider := hit.get("collider") as CollisionObject3D
	return collider != null and str(collider.get_meta("fixture_kind", "")) == "floor"


func _read_output_path() -> String:
	var args := OS.get_cmdline_user_args()
	var index := args.find("--output")
	if index < 0 or index + 1 >= args.size():
		return ""
	return args[index + 1]
