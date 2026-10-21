"""Core data types shared across the pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Session(BaseModel):
    """An authenticated account the verifier holds."""

    label: str
    user_id: str
    token: str


class WriteProbe(BaseModel):
    """A benign change the verifier can make, to test whether a record can be
    modified by someone who does not own it.

    `marker` is a value written into the record; seeing it on read-back proves the
    change actually landed (guards against endpoints that 200 but ignore the body).
    """

    method: str
    path: str
    payload: dict[str, Any] = Field(default_factory=dict)
    marker: str


class ResourceRef(BaseModel):
    """A record belonging to one account, worth probing for cross-account access.

    `marker` is a value unique to the owner's record (a nonce we set). If another
    account's response contains it, we have proof they reached data they do not own.
    `write`, when present, adds a mutation probe for the same record.
    """

    method: str
    path: str
    owner_id: str
    marker: str
    sample: dict[str, Any] = Field(default_factory=dict)
    write: WriteProbe | None = None


class Finding(BaseModel):
    """One verified problem, with the evidence that proves it."""

    check: Literal[
        "isolation",
        "unauthenticated",
        "cross_account_write",
        "unauthenticated_write",
        "regression",
    ] = "isolation"
    severity: Literal["high", "medium", "low"] = "high"
    title: str
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ScanResult(BaseModel):
    target: str
    resources_probed: int
    findings: list[Finding] = Field(default_factory=list)
    baseline_status: str | None = None
