from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Frame, Vec3


@dataclass(frozen=True, slots=True)
class Port:
    id: str
    owner_id: str
    frame: Frame
    connector_size: Vec3
    role: str


@dataclass(frozen=True, slots=True)
class BeamIntent:
    id: str
    start_port: str
    end_port: str
    width: float
    height: float
    category: str = "Railings"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PostIntent:
    id: str
    base: Vec3
    top_port: str
    width: float
    depth: float
    category: str = "Railings"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Connection:
    id: str
    port_ids: tuple[str, ...]
    mutual_insertion: bool = True
    orientation_mode: str = "opposed"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    connection_id: str | None = None
    element_ids: tuple[str, ...] = ()
    distance: float | None = None
    angle_degrees: float | None = None


@dataclass(slots=True)
class Model:
    ports: dict[str, Port] = field(default_factory=dict)
    beams: dict[str, BeamIntent] = field(default_factory=dict)
    posts: dict[str, PostIntent] = field(default_factory=dict)
    connections: dict[str, Connection] = field(default_factory=dict)

    def add_port(self, port: Port) -> None:
        if port.id in self.ports:
            raise ValueError(f"Duplicate port id: {port.id}")
        self.ports[port.id] = port

    def add_beam(self, beam: BeamIntent) -> None:
        if beam.id in self.beams or beam.id in self.posts:
            raise ValueError(f"Duplicate element id: {beam.id}")
        self.beams[beam.id] = beam

    def add_post(self, post: PostIntent) -> None:
        if post.id in self.beams or post.id in self.posts:
            raise ValueError(f"Duplicate element id: {post.id}")
        self.posts[post.id] = post

    def add_connection(self, connection: Connection) -> None:
        if connection.id in self.connections:
            raise ValueError(f"Duplicate connection id: {connection.id}")
        self.connections[connection.id] = connection
