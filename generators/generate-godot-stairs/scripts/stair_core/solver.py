from __future__ import annotations

from dataclasses import dataclass
import math

from .geometry import Frame, Mat3, Transform, Vec3, face_exit_distance, rotation_between_frames
from .model import BeamIntent, Model, Port, PostIntent


@dataclass(frozen=True, slots=True)
class CompiledBeam:
    id: str
    center: Vec3
    size: Vec3
    orientation: Mat3
    logical_start: Vec3
    logical_end: Vec3
    start_extension: float
    end_extension: float
    category: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class CompiledPost:
    id: str
    center: Vec3
    size: Vec3
    orientation: Mat3
    category: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class CompiledJoint:
    id: str
    center: Vec3
    size: Vec3
    orientation: Mat3
    category: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class CompiledSupportCap:
    id: str
    center: Vec3
    size: Vec3
    high_direction: Vec3
    category: str
    metadata: dict[str, object]


def connect(fixed: Port, moving_local: Port, oppose_forward: bool = True) -> Transform:
    """Return the only rigid transform aligning moving_local with fixed."""
    rotation = rotation_between_frames(moving_local.frame, fixed.frame, oppose_forward)
    translation = fixed.frame.position - rotation.apply(moving_local.frame.position)
    return Transform(rotation, translation)


def spatial_slope_beam_ports(model: Model, connection: object) -> list[Port]:
    """Return ports of an ambiguous beam-only spatial slope corner."""
    ports = [model.ports[port_id] for port_id in connection.port_ids]
    beam_ports = [port for port in ports if port.role == "beam_end" and port.owner_id in model.beams]
    if len(beam_ports) < 2 or len(beam_ports) != len(ports):
        return []
    for index, first in enumerate(beam_ports):
        first_direction = first.frame.orientation.forward
        first_horizontal = math.hypot(first_direction.x, first_direction.z)
        if abs(first_direction.y) <= 1.0e-9 or first_horizontal <= 1.0e-9:
            continue
        for second in beam_ports[index + 1:]:
            second_direction = second.frame.orientation.forward
            second_horizontal = math.hypot(second_direction.x, second_direction.z)
            if abs(second_direction.y) <= 1.0e-9 or second_horizontal <= 1.0e-9:
                continue
            plan_cross = abs(
                (first_direction.x / first_horizontal) * (second_direction.z / second_horizontal)
                - (first_direction.z / first_horizontal) * (second_direction.x / second_horizontal)
            )
            if plan_cross > 1.0e-6:
                return beam_ports
    return []


def _connection_extensions(model: Model) -> dict[str, float]:
    extensions = {port_id: 0.0 for port_id in model.ports}
    for connection in model.connections.values():
        if not connection.mutual_insertion:
            continue
        if spatial_slope_beam_ports(model, connection):
            raise ValueError(
                f"Connection {connection.id} is an ambiguous spatial slope corner; "
                "connect each beam to an explicit socketed post"
            )
        members = [model.ports[port_id] for port_id in connection.port_ids]
        if len(members) < 2:
            continue
        for moving in members:
            direction = moving.frame.orientation.forward
            moving_beam = model.beams.get(moving.owner_id)
            if moving_beam is None:
                moving_half_width = moving.connector_size.x * 0.5
                moving_half_height = moving.connector_size.y * 0.5
            else:
                moving_half_width = moving_beam.width * 0.5
                moving_half_height = moving_beam.height * 0.5
            for receiver in members:
                if receiver.id == moving.id:
                    continue
                distance = face_exit_distance(
                    receiver.frame,
                    receiver.connector_size * 0.5,
                    moving.frame,
                    moving_half_width,
                    moving_half_height,
                    direction,
                )
                extensions[moving.id] = max(extensions[moving.id], distance)
    return extensions


def _compile_beam(model: Model, beam: BeamIntent, extensions: dict[str, float]) -> CompiledBeam:
    start_port = model.ports[beam.start_port]
    end_port = model.ports[beam.end_port]
    logical_start = start_port.frame.position
    logical_end = end_port.frame.position
    axis = logical_end - logical_start
    logical_length = axis.length()
    if logical_length <= 1.0e-9:
        raise ValueError(f"Beam {beam.id} has coincident ports")
    direction = axis / logical_length
    start_extension = extensions.get(start_port.id, 0.0)
    end_extension = extensions.get(end_port.id, 0.0)
    physical_start = logical_start - direction * start_extension
    physical_end = logical_end + direction * end_extension
    center = (physical_start + physical_end) * 0.5
    orientation = Mat3.from_forward_up(direction)
    return CompiledBeam(
        beam.id,
        center,
        Vec3(beam.width, beam.height, physical_start.distance_to(physical_end)),
        orientation,
        logical_start,
        logical_end,
        start_extension,
        end_extension,
        beam.category,
        dict(beam.metadata),
    )


def _compile_post(model: Model, post: PostIntent) -> CompiledPost:
    port = model.ports[post.top_port]
    # The explicit top port is the maximum permitted post extent; no inferred
    # handrail centre or bounding-box correction is applied later.
    height = port.frame.position.y - post.base.y
    if height <= 1.0e-9:
        raise ValueError(f"Post {post.id} has non-positive height {height}")
    center = Vec3(post.base.x, post.base.y + height / 2.0, post.base.z)
    return CompiledPost(
        post.id,
        center,
        Vec3(post.width, height, post.depth),
        Mat3.identity(),
        post.category,
        dict(post.metadata),
    )


def compile_model(model: Model) -> tuple[list[CompiledBeam], list[CompiledPost]]:
    extensions = _connection_extensions(model)
    beams = [_compile_beam(model, beam, extensions) for beam in model.beams.values()]
    posts = [_compile_post(model, post) for post in model.posts.values()]
    return beams, posts


def compile_joint_hubs(model: Model) -> list[CompiledJoint]:
    """Compatibility hook; explicit topology now owns all visible joints."""
    return []


def compile_support_caps(
    model: Model,
    beams: list[CompiledBeam],
    posts: list[CompiledPost],
) -> list[CompiledSupportCap]:
    """Create a full-section block between a post and a sloped handrail."""
    beams_by_id = {beam.id: beam for beam in beams}
    posts_by_id = {post.id: post for post in posts}
    caps: list[CompiledSupportCap] = []
    for connection in model.connections.values():
        ports = [model.ports[port_id] for port_id in connection.port_ids]
        post_ports = [port for port in ports if port.role == "post_top" and port.owner_id in posts_by_id]
        support_ports = [port for port in ports if port.role == "beam_support" and port.owner_id in beams_by_id]
        for post_port in post_ports:
            post = posts_by_id[post_port.owner_id]
            for support_port in support_ports:
                beam = beams_by_id[support_port.owner_id]
                direction = beam.orientation.forward
                horizontal = math.hypot(direction.x, direction.z)
                if horizontal <= 1.0e-9 or abs(direction.y) <= 1.0e-9:
                    continue
                post_top = post.center.y + post.size.y * 0.5
                section = max(post.size.x, post.size.z, support_port.connector_size.x, support_port.connector_size.z)
                caps.append(
                    CompiledSupportCap(
                        f"SupportCap_{post.id}",
                        Vec3(post.center.x, post_top + section * 0.5, post.center.z),
                        Vec3(section, section, section),
                        Vec3(direction.x / horizontal, 0.0, direction.z / horizontal),
                        post.category,
                        {
                            "connection_id": connection.id,
                            "post_id": post.id,
                            "support_connector": True,
                            "connection_ports": (post_port.id, support_port.id),
                            **post.metadata,
                        },
                    )
                )
    return caps
