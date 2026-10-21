from __future__ import annotations

from dataclasses import dataclass
import math

from .geometry import EPSILON, Mat3, Vec3
from .model import Model, Port
from .solver import CompiledBeam


@dataclass(frozen=True, slots=True)
class FinishedBeam:
    """Final convex visual/collision body for one compiled beam.

    Faces are stored in world space.  The logical graph and compiled beam stay
    untouched; this type belongs solely to the deterministic mesh-finishing
    stage.
    """

    id: str
    faces: tuple[tuple[Vec3, ...], ...]
    clipped_by: tuple[str, ...] = ()

    @property
    def vertices(self) -> tuple[Vec3, ...]:
        unique: list[Vec3] = []
        for face in self.faces:
            for point in face:
                if not any(point.distance_to(existing) <= 1.0e-8 for existing in unique):
                    unique.append(point)
        return tuple(unique)


@dataclass(frozen=True, slots=True)
class JointFinishReport:
    clipped_elements: tuple[str, ...]
    operations: tuple[dict[str, object], ...]


def _box_faces(beam: CompiledBeam) -> tuple[tuple[Vec3, ...], ...]:
    hx, hy, hz = beam.size.x * 0.5, beam.size.y * 0.5, beam.size.z * 0.5
    local = (
        Vec3(-hx, -hy, -hz), Vec3(hx, -hy, -hz),
        Vec3(hx, hy, -hz), Vec3(-hx, hy, -hz),
        Vec3(-hx, -hy, hz), Vec3(hx, -hy, hz),
        Vec3(hx, hy, hz), Vec3(-hx, hy, hz),
    )
    points = tuple(beam.center + beam.orientation.apply(point) for point in local)
    return tuple(
        tuple(points[index] for index in indices)
        for indices in (
            (0, 3, 2, 1), (4, 5, 6, 7),
            (0, 4, 7, 3), (1, 2, 6, 5),
            (0, 1, 5, 4), (3, 7, 6, 2),
        )
    )


def _deduplicate(points: list[Vec3], tolerance: float = 1.0e-8) -> list[Vec3]:
    result: list[Vec3] = []
    for point in points:
        if not any(point.distance_to(existing) <= tolerance for existing in result):
            result.append(point)
    return result


def _ordered_cap(points: list[Vec3], outward: Vec3) -> tuple[Vec3, ...]:
    points = _deduplicate(points)
    if len(points) < 3:
        return ()
    center = sum(points[1:], points[0]) / float(len(points))
    helper = Vec3(1.0, 0.0, 0.0) if abs(outward.x) < 0.8 else Vec3(0.0, 1.0, 0.0)
    first_axis = outward.cross(helper).normalized()
    second_axis = outward.cross(first_axis).normalized()
    ordered = sorted(
        points,
        key=lambda point: math.atan2(
            (point - center).dot(second_axis),
            (point - center).dot(first_axis),
        ),
    )
    normal = (ordered[1] - ordered[0]).cross(ordered[2] - ordered[0])
    if normal.dot(outward) < 0.0:
        ordered.reverse()
    return tuple(ordered)


def clip_convex_faces(
    faces: tuple[tuple[Vec3, ...], ...],
    plane_point: Vec3,
    keep_normal: Vec3,
) -> tuple[tuple[Vec3, ...], ...]:
    """Clip a convex polyhedron, retaining the positive plane half-space."""
    normal = keep_normal.normalized()
    clipped_faces: list[tuple[Vec3, ...]] = []
    intersections: list[Vec3] = []
    tolerance = 1.0e-8
    for face in faces:
        output: list[Vec3] = []
        for index, current in enumerate(face):
            previous = face[index - 1]
            current_distance = (current - plane_point).dot(normal)
            previous_distance = (previous - plane_point).dot(normal)
            current_inside = current_distance >= -tolerance
            previous_inside = previous_distance >= -tolerance
            if current_inside != previous_inside:
                denominator = previous_distance - current_distance
                if abs(denominator) > EPSILON:
                    ratio = previous_distance / denominator
                    crossing = previous + (current - previous) * ratio
                    output.append(crossing)
                    intersections.append(crossing)
            if current_inside:
                output.append(current)
        output = _deduplicate(output)
        if len(output) >= 3:
            clipped_faces.append(tuple(output))
    cap = _ordered_cap(intersections, normal * -1.0)
    if cap:
        clipped_faces.append(cap)
    return tuple(clipped_faces)


def _receiver_boundary(
    receiver: CompiledBeam,
    joint: Vec3,
    toward_incoming_body: Vec3,
) -> tuple[Vec3, Vec3] | None:
    """Return the first receiver face reached from a joint toward the incoming body."""
    local_origin = receiver.orientation.transpose_apply(joint - receiver.center)
    local_direction = receiver.orientation.transpose_apply(toward_incoming_body.normalized())
    half = receiver.size * 0.5
    axes = (
        (local_origin.x, local_direction.x, half.x, receiver.orientation.right),
        (local_origin.y, local_direction.y, half.y, receiver.orientation.up),
        (local_origin.z, local_direction.z, half.z, receiver.orientation.forward),
    )
    candidates: list[tuple[float, Vec3, Vec3]] = []
    for origin, velocity, extent, world_axis in axes:
        if abs(velocity) <= EPSILON:
            continue
        sign = 1.0 if velocity > 0.0 else -1.0
        distance = (sign * extent - origin) / velocity
        if distance < -1.0e-8:
            continue
        hit = local_origin + local_direction * max(0.0, distance)
        if (
            abs(hit.x) <= half.x + 1.0e-8
            and abs(hit.y) <= half.y + 1.0e-8
            and abs(hit.z) <= half.z + 1.0e-8
        ):
            normal = world_axis * sign
            candidates.append((max(0.0, distance), receiver.center + receiver.orientation.apply(hit), normal))
    if not candidates:
        return None
    _distance, point, normal = min(candidates, key=lambda item: item[0])
    return point, normal


def _through_priority(beam: CompiledBeam) -> tuple[int, float, str]:
    horizontal = abs(beam.orientation.forward.y) <= 1.0e-8
    return (0 if horizontal else 1, -beam.size.z, beam.id)


def _face_plane(face: tuple[Vec3, ...]) -> tuple[Vec3, Vec3]:
    normal = (face[1] - face[0]).cross(face[2] - face[0]).normalized()
    return face[0], normal


def _split_polygon(
    polygon: tuple[Vec3, ...],
    plane_point: Vec3,
    outward: Vec3,
) -> tuple[tuple[Vec3, ...], tuple[Vec3, ...]]:
    """Return polygon portions inside and outside an outward-facing plane."""
    inside: list[Vec3] = []
    outside: list[Vec3] = []
    tolerance = 1.0e-8
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        current_distance = (current - plane_point).dot(outward)
        previous_distance = (previous - plane_point).dot(outward)
        current_inside = current_distance <= tolerance
        previous_inside = previous_distance <= tolerance
        if current_inside != previous_inside:
            denominator = previous_distance - current_distance
            if abs(denominator) > EPSILON:
                ratio = previous_distance / denominator
                crossing = previous + (current - previous) * ratio
                inside.append(crossing)
                outside.append(crossing)
        (inside if current_inside else outside).append(current)
    inside = _deduplicate(inside)
    outside = _deduplicate(outside)
    return (
        tuple(inside) if len(inside) >= 3 else (),
        tuple(outside) if len(outside) >= 3 else (),
    )


def subtract_convex_from_polygon(
    polygon: tuple[Vec3, ...],
    receiver_faces: tuple[tuple[Vec3, ...], ...],
) -> tuple[tuple[Vec3, ...], ...]:
    """Remove the portion of a polygon strictly inside a convex receiver."""
    planes = tuple(_face_plane(face) for face in receiver_faces)
    # Coincident boundary faces belong to the union surface and must not both
    # disappear.  Their duplicate is harmless; deleting both would make a hole.
    for plane_point, outward in planes:
        distances = [(point - plane_point).dot(outward) for point in polygon]
        if all(abs(distance) <= 1.0e-8 for distance in distances):
            return (polygon,)
    candidates: list[tuple[Vec3, ...]] = [polygon]
    exterior: list[tuple[Vec3, ...]] = []
    for plane_point, outward in planes:
        next_candidates: list[tuple[Vec3, ...]] = []
        for candidate in candidates:
            inside, outside = _split_polygon(candidate, plane_point, outward)
            if outside:
                exterior.append(outside)
            if inside:
                next_candidates.append(inside)
        candidates = next_candidates
        if not candidates:
            break
    # What remains in candidates lies inside all receiver planes and is the
    # internal surface removed by the Boolean union.
    return tuple(exterior)


def finish_beam_joints(
    model: Model,
    beams: list[CompiledBeam],
) -> tuple[dict[str, FinishedBeam], JointFinishReport]:
    """Create exposed union surfaces without changing ports or transforms.

    Connected boxes retain their compiled volumes.  Only surface fragments that
    lie strictly inside a connected neighbour are removed.  This is a true
    surface union: it keeps the approved outer silhouette and eliminates the
    internal/protruding faces that made otherwise-correct joints look broken.
    The overlapping original boxes remain an exact decomposition of the same
    union for static collision.
    """
    by_id = {beam.id: beam for beam in beams}
    original_faces = {beam.id: _box_faces(beam) for beam in beams}
    neighbours: dict[str, set[str]] = {beam.id: set() for beam in beams}
    connections_by_pair: dict[tuple[str, str], list[str]] = {}
    operations: list[dict[str, object]] = []
    for connection in sorted(model.connections.values(), key=lambda item: item.id):
        beam_ports = [
            model.ports[port_id]
            for port_id in connection.port_ids
            if model.ports[port_id].role == "beam_end"
            and model.ports[port_id].owner_id in by_id
        ]
        owners = sorted({port.owner_id for port in beam_ports})
        if not connection.mutual_insertion or len(owners) < 2:
            continue
        through_id = min(owners, key=lambda owner: _through_priority(by_id[owner]))
        for index, first in enumerate(owners):
            for second in owners[index + 1:]:
                neighbours[first].add(second)
                neighbours[second].add(first)
                connections_by_pair.setdefault(tuple(sorted((first, second))), []).append(connection.id)
        operations.append(
            {
                "connection_id": connection.id,
                "through_id": through_id,
                "member_ids": owners,
                "operation": "surface_union",
            }
        )

    faces: dict[str, tuple[tuple[Vec3, ...], ...]] = {}
    finished_by: dict[str, tuple[str, ...]] = {}
    for beam in beams:
        fragments: list[tuple[Vec3, ...]] = list(original_faces[beam.id])
        connection_ids: set[str] = set()
        for neighbour_id in sorted(neighbours[beam.id]):
            reduced: list[tuple[Vec3, ...]] = []
            for fragment in fragments:
                reduced.extend(subtract_convex_from_polygon(fragment, original_faces[neighbour_id]))
            fragments = reduced
            connection_ids.update(connections_by_pair[tuple(sorted((beam.id, neighbour_id)))])
        faces[beam.id] = tuple(fragments)
        finished_by[beam.id] = tuple(sorted(connection_ids)) if tuple(fragments) != original_faces[beam.id] else ()

    finished = {
        beam.id: FinishedBeam(beam.id, faces[beam.id], finished_by[beam.id])
        for beam in beams
    }
    clipped_elements = tuple(sorted(beam_id for beam_id, joints in finished_by.items() if joints))
    return finished, JointFinishReport(clipped_elements, tuple(operations))
