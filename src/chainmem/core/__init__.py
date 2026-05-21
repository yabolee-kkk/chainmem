"""数据模型：ChainNode 和 Chain"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import uuid
import numpy as np


@dataclass
class ChainNode:
    """链节点——记忆的最小单元"""
    id: str
    chain_id: str
    seq: int
    text: str
    embedding: np.ndarray | None = None  # shape=(d,)，运行时内存中
    prev_id: str | None = None
    next_id: str | None = None

    @property
    def text_prefix(self) -> str:
        return self.text[:3] if len(self.text) >= 3 else self.text


@dataclass
class Chain:
    """链——整段记忆的元信息"""
    id: str
    root_id: str
    leaf_id: str
    anchor_prefix: str
    node_count: int
    nodes: list[ChainNode] = field(default_factory=list)
    summary: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)
    strength: float = 1.0

    @classmethod
    def from_nodes(cls, nodes: list[ChainNode]) -> "Chain":
        if not nodes:
            raise ValueError("Cannot create Chain from empty nodes list")
        chain_id = nodes[0].chain_id
        return cls(
            id=chain_id,
            root_id=nodes[0].id,
            leaf_id=nodes[-1].id,
            anchor_prefix=nodes[0].text_prefix,
            node_count=len(nodes),
            nodes=nodes,
        )

    def full_text(self) -> str:
        """拼接整条链的完整文本"""
        return "".join(node.text for node in self.nodes)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_count": self.node_count,
            "anchor_prefix": self.anchor_prefix,
            "source": self.source,
            "tags": self.tags,
            "strength": self.strength,
            "full_text": self.full_text(),
        }
