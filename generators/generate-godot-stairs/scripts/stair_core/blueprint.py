from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Frame, Vec3, Y_AXIS
from .model import BeamIntent, Connection, Model, Port, PostIntent


@dataclass(slots=True)
class Blueprint:
    """Declarative structure builder; contains no Godot scene operations."""

    model: Model = field(default_factory=Model)
    joint_members: dict[str, list[str]] = field(default_factory=dict)

    def add_beam(
        self,
        element_id: str,
        start: Vec3,
        end: Vec3,
        width: float,
        height: float,
        start_joint: str | None,
        end_joint: str | None,
        metadata: dict[str, object] | None = None,
    ) -> BeamIntent:
        direction = (end - start).normalized()
        connector_size = Vec3(width, height, max(width, height))
        start_port = Port(
            f"{element_id}.start",
            element_id,
            Frame.from_forward_up(start, direction * -1.0),
            connector_size,
            "beam_end",
        )
        end_port = Port(
            f"{element_id}.end",
            element_id,
            Frame.from_forward_up(end, direction),
            connector_size,
            "beam_end",
        )
        self.model.add_port(start_port)
        self.model.add_port(end_port)
        beam = BeamIntent(
            element_id,
            start_port.id,
            end_port.id,
            width,
            height,
            metadata=dict(metadata or {}),
        )
        self.model.add_beam(beam)
        if start_joint:
            self.joint_members.setdefault(start_joint, []).append(start_port.id)
        if end_joint:
            self.joint_members.setdefault(end_joint, []).append(end_port.id)
        return beam

    def add_post_support(
        self,
        post_id: str,
        beam_id: str,
        base: Vec3,
        support_point: Vec3,
        width: float,
        depth: float,
        metadata: dict[str, object] | None = None,
    ) -> PostIntent:
        top_port = Port(
            f"{post_id}.top",
            post_id,
            Frame.from_forward_up(support_point, Y_AXIS),
            Vec3(width, width, depth),
            "post_top",
        )
        support_port = Port(
            f"{beam_id}.support.{post_id}",
            beam_id,
            Frame.from_forward_up(support_point, Y_AXIS * -1.0),
            Vec3(width, width, depth),
            "beam_support",
        )
        self.model.add_port(top_port)
        self.model.add_port(support_port)
        post = PostIntent(
            post_id,
            base,
            top_port.id,
            width,
            depth,
            metadata=dict(metadata or {}),
        )
        self.model.add_post(post)
        self.model.add_connection(
            Connection(
                f"support:{post_id}",
                (support_port.id, top_port.id),
                mutual_insertion=False,
                orientation_mode="opposed",
            )
        )
        return post

    def add_terminal_post(
        self,
        post_id: str,
        beam_port_id: str,
        base: Vec3,
        width: float,
        depth: float,
        metadata: dict[str, object] | None = None,
    ) -> PostIntent:
        """Join a sloped rail to a post through a standard horizontal adapter."""
        beam_port = self.model.ports[beam_port_id]
        beam_forward = beam_port.frame.orientation.forward
        adapter_direction = Vec3(beam_forward.x, 0.0, beam_forward.z).normalized()
        adapter_id = f"{post_id}.terminal_adapter"
        adapter_end = beam_port.frame.position + adapter_direction * (width * 1.5)
        adapter = self.add_beam(
            adapter_id,
            beam_port.frame.position,
            adapter_end,
            width,
            width,
            None,
            None,
            {**dict(metadata or {}), "terminal_adapter": True},
        )
        self.model.add_connection(
            Connection(
                f"terminal:{post_id}",
                (beam_port_id, adapter.start_port),
                mutual_insertion=True,
                orientation_mode="any",
            )
        )
        support_point = Vec3(
            beam_port.frame.position.x,
            beam_port.frame.position.y - width * 0.5,
            beam_port.frame.position.z,
        )
        return self.add_post_support(
            post_id,
            adapter_id,
            base,
            support_point,
            width,
            depth,
            metadata,
        )

    def add_socketed_post(
        self,
        post_id: str,
        base: Vec3,
        top: Vec3,
        width: float,
        depth: float,
        beam_port_ids: tuple[str, ...],
        metadata: dict[str, object] | None = None,
    ) -> PostIntent:
        """Add one post with an explicit side socket for every attached beam."""
        highest_socket_top = max(
            self.model.ports[port_id].frame.position.y + width * 0.5
            for port_id in beam_port_ids
        )
        if top.y < highest_socket_top - 1.0e-9:
            raise ValueError(
                f"Socketed post {post_id} top {top.y:.9f} is below its socket volume top "
                f"{highest_socket_top:.9f}"
            )
        top_port = Port(
            f"{post_id}.top",
            post_id,
            Frame.from_forward_up(top, Y_AXIS),
            Vec3(width, width, depth),
            "post_top",
        )
        self.model.add_port(top_port)
        post = PostIntent(
            post_id,
            base,
            top_port.id,
            width,
            depth,
            metadata=dict(metadata or {}),
        )
        self.model.add_post(post)
        for index, beam_port_id in enumerate(beam_port_ids, 1):
            beam_port = self.model.ports[beam_port_id]
            beam_forward = beam_port.frame.orientation.forward
            socket_normal = Vec3(beam_forward.x, 0.0, beam_forward.z).normalized() * -1.0
            socket = Port(
                f"{post_id}.socket.{index:02d}",
                post_id,
                Frame.from_forward_up(
                    beam_port.frame.position,
                    socket_normal,
                ),
                Vec3(width, width, depth),
                "post_socket",
            )
            self.model.add_port(socket)
            self.model.add_connection(
                Connection(
                    f"socket:{post_id}:{index:02d}",
                    (beam_port_id, socket.id),
                    mutual_insertion=True,
                    orientation_mode="any",
                )
            )
        return post

    def finish(self) -> Model:
        for joint_id, port_ids in sorted(self.joint_members.items()):
            if len(port_ids) < 2:
                continue
            self.model.add_connection(
                Connection(
                    joint_id,
                    tuple(port_ids),
                    mutual_insertion=True,
                    orientation_mode="any",
                )
            )
        return self.model
