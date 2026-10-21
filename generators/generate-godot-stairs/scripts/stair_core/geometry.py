from __future__ import annotations

from dataclasses import dataclass
import math


EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float
    y: float
    z: float

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, value: float) -> "Vec3":
        return Vec3(self.x * value, self.y * value, self.z * value)

    __rmul__ = __mul__

    def __truediv__(self, value: float) -> "Vec3":
        return Vec3(self.x / value, self.y / value, self.z / value)

    def dot(self, other: "Vec3") -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vec3") -> "Vec3":
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> "Vec3":
        length = self.length()
        if length <= EPSILON:
            raise ValueError(f"Cannot normalize zero-length vector {self}")
        return self / length

    def distance_to(self, other: "Vec3") -> float:
        return (self - other).length()

    def tuple(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


X_AXIS = Vec3(1.0, 0.0, 0.0)
Y_AXIS = Vec3(0.0, 1.0, 0.0)
Z_AXIS = Vec3(0.0, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class Mat3:
    """Orthonormal basis stored as right, up, forward columns."""

    right: Vec3
    up: Vec3
    forward: Vec3

    @classmethod
    def identity(cls) -> "Mat3":
        return cls(X_AXIS, Y_AXIS, Z_AXIS)

    @classmethod
    def from_forward_up(cls, forward: Vec3, up_hint: Vec3 = Y_AXIS) -> "Mat3":
        forward_n = forward.normalized()
        right = up_hint.cross(forward_n)
        if right.length() <= EPSILON:
            fallback = X_AXIS if abs(forward_n.x) < 0.9 else Z_AXIS
            right = fallback.cross(forward_n)
        right_n = right.normalized()
        up_n = forward_n.cross(right_n).normalized()
        return cls(right_n, up_n, forward_n)

    def apply(self, value: Vec3) -> Vec3:
        return self.right * value.x + self.up * value.y + self.forward * value.z

    def transpose_apply(self, value: Vec3) -> Vec3:
        return Vec3(value.dot(self.right), value.dot(self.up), value.dot(self.forward))

    def compose(self, other: "Mat3") -> "Mat3":
        return Mat3(self.apply(other.right), self.apply(other.up), self.apply(other.forward))

    def transpose(self) -> "Mat3":
        return Mat3(
            Vec3(self.right.x, self.up.x, self.forward.x),
            Vec3(self.right.y, self.up.y, self.forward.y),
            Vec3(self.right.z, self.up.z, self.forward.z),
        )


@dataclass(frozen=True, slots=True)
class Frame:
    position: Vec3
    orientation: Mat3

    @classmethod
    def from_forward_up(
        cls,
        position: Vec3,
        forward: Vec3,
        up_hint: Vec3 = Y_AXIS,
    ) -> "Frame":
        return cls(position, Mat3.from_forward_up(forward, up_hint))


@dataclass(frozen=True, slots=True)
class Transform:
    rotation: Mat3
    translation: Vec3

    @classmethod
    def identity(cls) -> "Transform":
        return cls(Mat3.identity(), Vec3(0.0, 0.0, 0.0))

    def point(self, value: Vec3) -> Vec3:
        return self.rotation.apply(value) + self.translation

    def direction(self, value: Vec3) -> Vec3:
        return self.rotation.apply(value)

    def frame(self, value: Frame) -> Frame:
        return Frame(self.point(value.position), self.rotation.compose(value.orientation))

    def compose(self, other: "Transform") -> "Transform":
        return Transform(
            self.rotation.compose(other.rotation),
            self.point(other.translation),
        )


def rotation_between_frames(local: Frame, target: Frame, oppose_forward: bool) -> Mat3:
    target_orientation = target.orientation
    if oppose_forward:
        target_orientation = Mat3(
            target.orientation.right * -1.0,
            target.orientation.up,
            target.orientation.forward * -1.0,
        )
    return target_orientation.compose(local.orientation.transpose())


def angle_degrees(first: Vec3, second: Vec3) -> float:
    cosine = max(-1.0, min(1.0, first.normalized().dot(second.normalized())))
    return math.degrees(math.acos(cosine))


def intersect_axes_in_plan(
    first_start: Vec3,
    first_end: Vec3,
    second_start: Vec3,
    second_end: Vec3,
) -> tuple[Vec3, Vec3] | None:
    """Return equal-XZ points on two infinite 3D axes, preserving each Y."""
    first = first_end - first_start
    second = second_end - second_start
    denominator = first.x * second.z - first.z * second.x
    if abs(denominator) <= EPSILON:
        return None
    offset = second_start - first_start
    first_t = (offset.x * second.z - offset.z * second.x) / denominator
    second_t = (offset.x * first.z - offset.z * first.x) / denominator
    return first_start + first * first_t, second_start + second * second_t


def ray_exit_distance(frame: Frame, half_extents: Vec3, direction: Vec3) -> float:
    """Distance from a frame origin to its box boundary along direction."""
    local = frame.orientation.transpose_apply(direction.normalized())
    distances = [
        extent / abs(component)
        for extent, component in (
            (half_extents.x, local.x),
            (half_extents.y, local.y),
            (half_extents.z, local.z),
        )
        if abs(component) > EPSILON
    ]
    if not distances:
        raise ValueError("Direction cannot exit connector volume")
    return min(distances)


def face_exit_distance(
    receiver: Frame,
    receiver_half_extents: Vec3,
    moving: Frame,
    moving_half_width: float,
    moving_half_height: float,
    direction: Vec3,
) -> float:
    """Safe insertion of a rectangular end face into an oriented box.

    The old centre-ray calculation could keep the beam axis inside a receiver
    while a corner of its end face had already crossed a far face.  A physical
    beam is limited by its complete end face, so the first initially-contained
    corner to leave the receiver is the deterministic insertion limit.
    """
    local_direction = receiver.orientation.transpose_apply(direction.normalized())
    exits: list[float] = []
    for width_sign in (-1.0, 1.0):
        for height_sign in (-1.0, 1.0):
            offset = (
                moving.orientation.right * (moving_half_width * width_sign)
                + moving.orientation.up * (moving_half_height * height_sign)
            )
            local_corner = receiver.orientation.transpose_apply(
                moving.position + offset - receiver.position
            )
            if any(
                abs(component) > extent + EPSILON
                for component, extent in (
                    (local_corner.x, receiver_half_extents.x),
                    (local_corner.y, receiver_half_extents.y),
                    (local_corner.z, receiver_half_extents.z),
                )
            ):
                # This corner is outside the receiver laterally at the logical
                # node and cannot define a valid far-boundary insertion limit.
                continue
            corner_exits = []
            for component, velocity, extent in (
                (local_corner.x, local_direction.x, receiver_half_extents.x),
                (local_corner.y, local_direction.y, receiver_half_extents.y),
                (local_corner.z, local_direction.z, receiver_half_extents.z),
            ):
                if velocity > EPSILON:
                    corner_exits.append((extent - component) / velocity)
                elif velocity < -EPSILON:
                    corner_exits.append((-extent - component) / velocity)
            nonnegative = [distance for distance in corner_exits if distance >= -EPSILON]
            if nonnegative:
                exits.append(max(0.0, min(nonnegative)))
    # A skew face with no corner inside the receiver must not be extended based
    # on its centre alone.  Keeping the logical endpoint is the safe fallback.
    return min(exits) if exits else 0.0


def lowest_beam_underside_over_footprint(
    axis_point: Vec3,
    axis_direction: Vec3,
    beam_height: float,
    footprint_half_x: float,
    footprint_half_z: float,
) -> Vec3:
    """Lowest underside height of a sloped rectangular beam over a footprint."""
    direction = axis_direction.normalized()
    horizontal = math.hypot(direction.x, direction.z)
    if horizontal <= EPSILON:
        raise ValueError("A vertical beam cannot define a horizontal support underside")
    slope_x = direction.y * direction.x / (horizontal * horizontal)
    slope_z = direction.y * direction.z / (horizontal * horizontal)
    corner_drop = abs(slope_x) * footprint_half_x + abs(slope_z) * footprint_half_z
    vertical_half_height = beam_height / (2.0 * horizontal)
    return Vec3(
        axis_point.x,
        axis_point.y - vertical_half_height - corner_drop,
        axis_point.z,
    )
