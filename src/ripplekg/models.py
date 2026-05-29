"""Shared contracts used by every front door (scripts / notebook / FastAPI).

`EditResult` is the keystone: it is the API response, the demo's render payload,
and the script's printed output — one shape, three consumers. Keep it stable.
"""
from typing import Literal

from pydantic import BaseModel, Field

# (head, relation_type, tail)
Triple = tuple[str, str, str]


class EditOp(BaseModel):
    """A single synthetic corpus edit (option A: caller supplies intended triples)."""
    doc_id: str
    sent_idx: int
    new_text: str
    intended_triples: list[Triple] = Field(default_factory=list)


class EvidenceDelta(BaseModel):
    """M1 output: one evidence record changed for an affected object."""
    delta_type: Literal["added", "removed", "unchanged"]
    scope: Literal["mention", "relation"]
    triple: dict = Field(default_factory=dict)
    target_id: str
    reason: str = ""


class Decision(BaseModel):
    """M2 output: cost-aware refresh decision for one KG object."""
    target_type: Literal["entity", "relation"]
    target_id: str
    decision: Literal["SKIP", "PATCH", "REBUILD"]
    reason: str = ""
    cost: int = 0


class EditResult(BaseModel):
    """The single contract returned by pipeline.run_edit()."""
    step: int
    edit: dict = Field(default_factory=dict)
    evidence_delta: list[EvidenceDelta] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    freshness: dict = Field(default_factory=lambda: {"marked_stale": [], "refreshed": []})
    cost: dict = Field(default_factory=lambda: {"this_step": 0, "vs_full_rebuild": 0})


class Node(BaseModel):
    id: str
    label: str
    type: str = ""
    freshness: str = "fresh"


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    freshness: str = "fresh"


class GraphView(BaseModel):
    """What GET /graph returns; vis-network renders nodes + edges directly."""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
