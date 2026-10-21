from __future__ import annotations

from dataclasses import dataclass

from .geometry import Vec3


@dataclass(frozen=True, slots=True)
class NodeRequest:
    id: str
    tier: str
    position: Vec3
    priority: int = 0


@dataclass(frozen=True, slots=True)
class ResolvedNode:
    id: str
    position: Vec3
    member_ids: tuple[str, ...]


def resolve_nearby_nodes(requests: list[NodeRequest], tolerance: float) -> tuple[dict[str, Vec3], tuple[ResolvedNode, ...]]:
    """Deterministically merge explicitly requested nodes within tolerance.

    The canonical location is supplied by the lowest-priority request, then by
    lexical id. This is a topology operation performed before mesh generation.
    """
    ordered = sorted(requests, key=lambda item: (item.tier, item.priority, item.id))
    remaining = set(range(len(ordered)))
    mapping: dict[str, Vec3] = {}
    resolved: list[ResolvedNode] = []
    while remaining:
        seed_index = min(remaining)
        seed = ordered[seed_index]
        cluster = {index for index in remaining if ordered[index].tier == seed.tier and ordered[index].position.distance_to(seed.position) <= tolerance}
        # Close transitively so results do not depend on insertion order.
        changed = True
        while changed:
            changed = False
            for index in tuple(remaining - cluster):
                candidate = ordered[index]
                if candidate.tier != seed.tier:
                    continue
                if any(candidate.position.distance_to(ordered[member].position) <= tolerance for member in cluster):
                    cluster.add(index)
                    changed = True
        members = sorted((ordered[index] for index in cluster), key=lambda item: (item.priority, item.id))
        canonical = members[0].position
        node_id = "node:" + members[0].tier + ":" + members[0].id
        member_ids = tuple(member.id for member in members)
        for member in members:
            mapping[member.id] = canonical
        resolved.append(ResolvedNode(node_id, canonical, member_ids))
        remaining.difference_update(cluster)
    return mapping, tuple(sorted(resolved, key=lambda node: node.id))
