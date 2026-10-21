"""Submission API: paste a URL, get a report — no terminal required.

This is the thin web layer the audience actually touches. It does the two things a
non-developer needs:

  1. accept a URL and run the whole check (ownership → isolation → behaviour);
  2. when ownership isn't proven yet, hand back the exact token to publish and a
     way to try again — never scan an app whose owner hasn't asked.

The verification itself is injected as two callables so the layer is testable
without the network; the default wiring uses the real `verify_ownership` and
`scan_url`.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .behavior import BaselineStore
from .browser_door import scan_browser
from .ownership import OwnershipResult, is_private_target, verify_ownership
from .report import render
from .store import Submission, SubmissionStore
from .url_scan import UrlScan, scan_url

STATUS_OWNERSHIP_REQUIRED = "ownership_required"
STATUS_UNSUPPORTED = "unsupported_stack"
STATUS_BLOCKED = "blocked_target"
STATUS_COMPLETE = "complete"
STATUS_ERROR = "error"

OwnershipFn = Callable[[str], Awaitable[OwnershipResult]]
ScanFn = Callable[[str], Awaitable["UrlScan | None"]]


def default_state_dir() -> Path:
    return Path(
        os.environ.get("TENANTCHECK_STATE_DIR") or (Path.home() / ".tenantcheck")
    )


class SubmissionService:
    def __init__(
        self,
        store: SubmissionStore,
        *,
        ownership: OwnershipFn | None = None,
        scan: ScanFn | None = None,
    ) -> None:
        self.store = store
        self._ownership = ownership or (lambda url: verify_ownership(url))
        self._scan = scan or (lambda url: scan_url(url))

    async def submit(self, url: str) -> Submission:
        return await self._run(self.store.create(url))

    async def resubmit(self, submission_id: str) -> Submission:
        record = self.store.get(submission_id)
        if record is None:
            raise KeyError(submission_id)
        record = replace(record, status="pending")
        return await self._run(record)

    async def _run(self, record: Submission) -> Submission:
        if is_private_target(record.url):
            record.status = STATUS_BLOCKED
            record.instructions = (
                "Local and private-network apps are scanned on your own machine, "
                "not by the hosted service. Run: tenantcheck scan <url>"
            )
            return self.store.update(record)

        try:
            ownership = await self._ownership(record.url)
        except Exception as exc:  # noqa: BLE001 - any failure is a reportable state
            record.status = STATUS_ERROR
            record.instructions = f"We could not reach {record.url}: {exc}"
            return self.store.update(record)

        if not ownership.verified:
            record.status = STATUS_OWNERSHIP_REQUIRED
            record.ownership_method = None
            record.instructions = ownership.instructions()
            return self.store.update(record)

        record.ownership_method = ownership.method
        scan = await self._scan(record.url)
        if scan is None:
            record.status = STATUS_UNSUPPORTED
            record.instructions = (
                "We proved you own this app, but could not detect a supported "
                "backend yet (currently Supabase). Enable browser scanning "
                "(TENANTCHECK_BROWSER_SCAN=1) to cover any app."
            )
            return self.store.update(record)

        record.status = STATUS_COMPLETE
        record.baseline_status = scan.result.baseline_status
        record.findings_count = len(scan.result.findings)
        record.report = render(scan.result)
        return self.store.update(record)


def _browser_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("TENANTCHECK_BROWSER_SCAN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def default_service(
    state_dir: str | Path | None = None,
    *,
    browser: bool | None = None,
) -> SubmissionService:
    state = Path(state_dir) if state_dir is not None else default_state_dir()
    baseline = BaselineStore(state / "baselines")
    browser_scan = scan_browser if _browser_enabled(browser) else None
    return SubmissionService(
        SubmissionStore(state / "submissions.db"),
        ownership=lambda url: verify_ownership(url),
        scan=lambda url: scan_url(url, store=baseline, browser_scan=browser_scan),
    )


class SubmitRequest(BaseModel):
    url: str


class SubmissionOut(BaseModel):
    id: str
    url: str
    status: str
    ownership_method: str | None = None
    instructions: str | None = None
    baseline_status: str | None = None
    findings_count: int = 0
    report: str | None = None
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, submission: Submission) -> SubmissionOut:
        return cls(**submission.__dict__)


def create_app(service: SubmissionService | None = None) -> FastAPI:
    service = service or default_service()
    app = FastAPI(title="verifier", version="0.1.0")
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/submissions", response_model=SubmissionOut)
    async def submit(request: SubmitRequest) -> SubmissionOut:
        return SubmissionOut.of(await service.submit(request.url))

    @app.get("/api/submissions", response_model=list[SubmissionOut])
    async def list_submissions() -> list[SubmissionOut]:
        return [SubmissionOut.of(s) for s in service.store.all()]

    @app.get("/api/submissions/{submission_id}", response_model=SubmissionOut)
    async def get_submission(submission_id: str) -> SubmissionOut:
        record = service.store.get(submission_id)
        if record is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return SubmissionOut.of(record)

    @app.post("/api/submissions/{submission_id}/recheck", response_model=SubmissionOut)
    async def recheck(submission_id: str) -> SubmissionOut:
        try:
            return SubmissionOut.of(await service.resubmit(submission_id))
        except KeyError:
            raise HTTPException(status_code=404, detail="submission not found") from None

    return app


def app_factory() -> FastAPI:
    """Build the production app (for `uvicorn verifier.api:app_factory --factory`)."""
    return create_app()
