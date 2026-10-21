"""Deterministic geometry core for the Godot stair generator."""

from .geometry import Frame, Mat3, Transform, Vec3, angle_degrees, face_exit_distance, intersect_axes_in_plan, lowest_beam_underside_over_footprint
from .blueprint import Blueprint
from .model import BeamIntent, Connection, Model, Port, PostIntent, ValidationIssue
from .solver import CompiledBeam, CompiledJoint, CompiledPost, CompiledSupportCap, compile_joint_hubs, compile_model, compile_support_caps, connect
from .joint_finish import FinishedBeam, JointFinishReport, clip_convex_faces, finish_beam_joints
from .topology import NodeRequest, ResolvedNode, resolve_nearby_nodes
from .validation import ValidationResult, validate_compiled, validate_model

__all__ = [
    "BeamIntent",
    "Blueprint",
    "CompiledBeam",
    "CompiledJoint",
    "CompiledPost",
    "CompiledSupportCap",
    "FinishedBeam",
    "JointFinishReport",
    "Connection",
    "Frame",
    "Mat3",
    "Model",
    "Port",
    "PostIntent",
    "Transform",
    "ValidationIssue",
    "ValidationResult",
    "Vec3",
    "angle_degrees",
    "compile_model",
    "compile_joint_hubs",
    "compile_support_caps",
    "connect",
    "clip_convex_faces",
    "finish_beam_joints",
    "face_exit_distance",
    "intersect_axes_in_plan",
    "lowest_beam_underside_over_footprint",
    "validate_model",
    "validate_compiled",
    "NodeRequest",
    "ResolvedNode",
    "resolve_nearby_nodes",
]
