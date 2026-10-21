from __future__ import annotations

from dataclasses import asdict, dataclass

from .geometry import Vec3, angle_degrees, lowest_beam_underside_over_footprint
from .model import Model, ValidationIssue
from .solver import CompiledBeam, CompiledPost, spatial_slope_beam_ports


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...]
    connection_count: int
    maximum_position_error: float
    maximum_angle_error: float

    @property
    def ok(self) -> bool:
        return not self.issues

    def report(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "connection_count": self.connection_count,
            "maximum_position_error": self.maximum_position_error,
            "maximum_angle_error_degrees": self.maximum_angle_error,
            "issues": [asdict(issue) for issue in self.issues],
        }


def validate_model(
    model: Model,
    position_tolerance: float = 1.0e-6,
    angle_tolerance_degrees: float = 0.01,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    maximum_position_error = 0.0
    maximum_angle_error = 0.0
    referenced: set[str] = set()
    for connection in model.connections.values():
        missing = [port_id for port_id in connection.port_ids if port_id not in model.ports]
        if missing:
            issues.append(
                ValidationIssue(
                    "missing_port",
                    f"Connection {connection.id} references missing ports: {', '.join(missing)}",
                    connection.id,
                )
            )
            continue
        ports = [model.ports[port_id] for port_id in connection.port_ids]
        spatial_ports = spatial_slope_beam_ports(model, connection)
        if spatial_ports:
            owners = tuple(port.owner_id for port in spatial_ports)
            issues.append(
                ValidationIssue(
                    "ambiguous_spatial_slope_joint",
                    f"Connection {connection.id} joins spatial slopes directly; use one explicit socketed corner post",
                    connection.id,
                    owners,
                )
            )
        referenced.update(connection.port_ids)
        anchor = ports[0]
        for port in ports[1:]:
            distance = anchor.frame.position.distance_to(port.frame.position)
            actual_angle = angle_degrees(anchor.frame.orientation.forward, port.frame.orientation.forward)
            if connection.orientation_mode == "any":
                angle_error = 0.0
            elif connection.orientation_mode == "same":
                angle_error = actual_angle
            else:
                angle_error = abs(180.0 - actual_angle)
            maximum_position_error = max(maximum_position_error, distance)
            maximum_angle_error = max(maximum_angle_error, angle_error)
            owners = (anchor.owner_id, port.owner_id)
            if distance > position_tolerance:
                issues.append(
                    ValidationIssue(
                        "port_position_mismatch",
                        f"Connection {connection.id}: {anchor.id} and {port.id} differ by {distance:.9f} m",
                        connection.id,
                        owners,
                        distance=distance,
                        angle_degrees=angle_error,
                    )
                )
            if angle_error > angle_tolerance_degrees:
                issues.append(
                    ValidationIssue(
                        "port_orientation_mismatch",
                        f"Connection {connection.id}: orientation error is {angle_error:.6f} degrees",
                        connection.id,
                        owners,
                        distance=distance,
                        angle_degrees=angle_error,
                    )
                )
    for beam in model.beams.values():
        for port_id in (beam.start_port, beam.end_port):
            if port_id not in model.ports:
                issues.append(ValidationIssue("missing_element_port", f"Beam {beam.id} references {port_id}", element_ids=(beam.id,)))
    for post in model.posts.values():
        if post.top_port not in model.ports:
            issues.append(ValidationIssue("missing_element_port", f"Post {post.id} references {post.top_port}", element_ids=(post.id,)))
        else:
            top = model.ports[post.top_port].frame.position
            horizontal_error = ((top.x - post.base.x) ** 2 + (top.z - post.base.z) ** 2) ** 0.5
            if horizontal_error > position_tolerance:
                issues.append(
                    ValidationIssue(
                        "post_not_vertical",
                        f"Post {post.id}: top is horizontally displaced by {horizontal_error:.9f} m from its base",
                        element_ids=(post.id,),
                        distance=horizontal_error,
                    )
                )
    return ValidationResult(
        tuple(issues),
        len(model.connections),
        maximum_position_error,
        maximum_angle_error,
    )


def _segment_distance(a0, a1, b0, b1) -> float:
    """Shortest distance between finite 3D segments (Ericson formulation)."""
    u, v, w = a1 - a0, b1 - b0, a0 - b0
    a, b, c, d, e = u.dot(u), u.dot(v), v.dot(v), u.dot(w), v.dot(w)
    denominator = a * c - b * b
    if a <= 1.0e-12 and c <= 1.0e-12:
        return a0.distance_to(b0)
    if a <= 1.0e-12:
        s, t = 0.0, max(0.0, min(1.0, e / c))
    elif c <= 1.0e-12:
        s, t = max(0.0, min(1.0, -d / a)), 0.0
    else:
        s = max(0.0, min(1.0, (b * e - c * d) / denominator)) if abs(denominator) > 1.0e-12 else 0.0
        t = (b * s + e) / c
        if t < 0.0:
            t, s = 0.0, max(0.0, min(1.0, -d / a))
        elif t > 1.0:
            t, s = 1.0, max(0.0, min(1.0, (b - d) / a))
    return (w + u * s - v * t).length()


def validate_compiled(
    model: Model,
    beams: list[CompiledBeam],
    posts: list[CompiledPost],
    tolerance: float = 1.0e-6,
) -> ValidationResult:
    """Validate emitted dimensions and reject unplanned beam intersections."""
    issues: list[ValidationIssue] = []
    for beam in beams:
        if min(beam.size.x, beam.size.y, beam.size.z) <= tolerance:
            issues.append(ValidationIssue("non_positive_geometry", f"Beam {beam.id} has size {beam.size}", element_ids=(beam.id,)))
    for post in posts:
        if min(post.size.x, post.size.y, post.size.z) <= tolerance:
            issues.append(ValidationIssue("non_positive_geometry", f"Post {post.id} has size {post.size}", element_ids=(post.id,)))

    beams_by_id = {beam.id: beam for beam in beams}
    posts_by_id = {post.id: post for post in posts}
    for connection in model.connections.values():
        connection_ports = [model.ports[port_id] for port_id in connection.port_ids if port_id in model.ports]
        post_ports = [port for port in connection_ports if port.role == "post_top" and port.owner_id in posts_by_id]
        support_ports = [port for port in connection_ports if port.role == "beam_support" and port.owner_id in beams_by_id]
        for post_port in post_ports:
            post = posts_by_id[post_port.owner_id]
            actual_top = post.center.y + post.size.y / 2.0
            for support_port in support_ports:
                beam = beams_by_id[support_port.owner_id]
                direction = beam.orientation.forward
                horizontal_sq = direction.x * direction.x + direction.z * direction.z
                along = (
                    (post.center.x - beam.logical_start.x) * direction.x
                    + (post.center.z - beam.logical_start.z) * direction.z
                ) / horizontal_sq
                axis_point = Vec3(
                    post.center.x,
                    beam.logical_start.y + direction.y * along,
                    post.center.z,
                )
                permitted = lowest_beam_underside_over_footprint(
                    axis_point,
                    direction,
                    beam.size.y,
                    post.size.x / 2.0,
                    post.size.z / 2.0,
                ).y
                error = actual_top - permitted
                if abs(error) > tolerance:
                    code = "support_penetration" if error > 0.0 else "support_gap"
                    issues.append(
                        ValidationIssue(
                            code,
                            f"Support {connection.id}: post {post.id} top differs from beam {beam.id} underside by {error:.9f} m",
                            connection.id,
                            (post.id, beam.id),
                            distance=abs(error),
                        )
                    )

    connected_pairs: set[frozenset[str]] = set()
    connector_members: dict[str, set[str]] = {}
    transition_members: dict[str, set[str]] = {}
    for connection in model.connections.values():
        owners = [model.ports[port_id].owner_id for port_id in connection.port_ids if port_id in model.ports]
        for index, first in enumerate(owners):
            for second in owners[index + 1:]:
                connected_pairs.add(frozenset((first, second)))
        for owner in owners:
            if owner not in model.posts:
                if owner in model.beams and model.beams[owner].metadata.get("landing_transition"):
                    transition_members.setdefault(owner, set()).update(
                        candidate for candidate in owners if candidate in model.beams and candidate != owner
                    )
            else:
                connector_members.setdefault(owner, set()).update(
                    candidate for candidate in owners if candidate in model.beams
                )
    # Separate ports on one explicit connector intentionally coordinate their
    # attached members. This is a planned joint even though no beam-to-beam
    # connection exists in the abstract graph.
    for members in connector_members.values():
        ordered = sorted(members)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                connected_pairs.add(frozenset((first, second)))
    # A short, explicitly declared slope-to-flat transition can be narrower
    # than the two connector volumes it joins. Its two named neighbours then
    # touch through that member by design. This exemption is restricted to the
    # tagged transition and never applies to an arbitrary two-hop chain.
    for members in transition_members.values():
        ordered = sorted(members)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                connected_pairs.add(frozenset((first, second)))

    endpoints = {}
    for beam in beams:
        direction = beam.orientation.forward
        endpoints[beam.id] = (
            beam.center - direction * (beam.size.z / 2.0),
            beam.center + direction * (beam.size.z / 2.0),
        )
    for index, first in enumerate(beams):
        for second in beams[index + 1:]:
            if frozenset((first.id, second.id)) in connected_pairs:
                continue
            distance = _segment_distance(*endpoints[first.id], *endpoints[second.id])
            clearance = 0.5 * (max(first.size.x, first.size.y) + max(second.size.x, second.size.y))
            if distance < clearance - tolerance:
                issues.append(
                    ValidationIssue(
                        "unplanned_intersection",
                        f"Beams {first.id} and {second.id} overlap by approximately {clearance - distance:.6f} m",
                        element_ids=(first.id, second.id),
                        distance=distance,
                    )
                )
    return ValidationResult(tuple(issues), len(model.connections), 0.0, 0.0)
