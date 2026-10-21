"""A tiny invoice app with two modes, as bench ground truth.

- `vuln`:             GET/PATCH /api/invoices/{id} act on any record for any
                     signed-in user (broken access control - read *and* write).
- `secure` / `good`:  ownership is checked; every field returned (clean twin).
- `regressed_field`:  owner-checked, but GET /api/invoices/{id} silently drops `note`.
- `regressed_error`:  owner-checked, but GET /api/invoices/{id} returns 500.

Same surface, one behaviour's difference - so the bench measures recall (must find
the vuln / the regression) and false-positive rate (must stay silent otherwise).

NOTE: Pydantic models live at module scope on purpose. FastAPI cannot resolve
body models that are defined inside a factory function.
"""

from __future__ import annotations

import os
import secrets
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel


class Signup(BaseModel):
    email: str
    password: str


class InvoiceIn(BaseModel):
    amount: float
    note: str = ""


class InvoicePatch(BaseModel):
    note: str | None = None


def make_app(mode: str | None = None) -> FastAPI:
    mode = mode or os.environ.get("CONTROL_MODE", "vuln")
    app = FastAPI(title=f"control-{mode}")

    users: dict[str, dict[str, str]] = {}
    passwords: dict[str, tuple[str, str]] = {}
    invoices: dict[str, dict[str, object]] = {}

    def current_user(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "unauthorized")
        user = users.get(authorization.split(" ", 1)[1])
        if not user:
            raise HTTPException(401, "unauthorized")
        return user["user_id"]

    @app.post("/signup")
    def signup(body: Signup) -> dict[str, str]:
        if body.email in passwords:
            raise HTTPException(400, "exists")
        user_id = "user_" + uuid.uuid4().hex[:8]
        passwords[body.email] = (body.password, user_id)
        token = secrets.token_urlsafe(16)
        users[token] = {"user_id": user_id, "email": body.email}
        return {"token": token, "user_id": user_id}

    @app.post("/login")
    def login(body: Signup) -> dict[str, str]:
        record = passwords.get(body.email)
        if not record or record[0] != body.password:
            raise HTTPException(401, "bad credentials")
        token = secrets.token_urlsafe(16)
        users[token] = {"user_id": record[1], "email": body.email}
        return {"token": token, "user_id": record[1]}

    @app.post("/api/invoices")
    def create_invoice(body: InvoiceIn, user_id: str = Depends(current_user)) -> dict[str, object]:
        invoice_id = "inv_" + uuid.uuid4().hex[:8]
        invoices[invoice_id] = {
            "id": invoice_id,
            "owner_id": user_id,
            "amount": body.amount,
            "note": body.note,
        }
        return invoices[invoice_id]

    @app.get("/api/invoices/{invoice_id}")
    def get_invoice(invoice_id: str, user_id: str = Depends(current_user)) -> dict[str, object]:
        invoice = invoices.get(invoice_id)
        if not invoice:
            raise HTTPException(404, "not found")
        if mode != "vuln" and invoice["owner_id"] != user_id:
            raise HTTPException(404, "not found")
        if mode == "regressed_error":
            raise HTTPException(500, "internal error")
        if mode == "regressed_field":
            return {k: v for k, v in invoice.items() if k != "note"}
        return invoice

    @app.patch("/api/invoices/{invoice_id}")
    def update_invoice(
        invoice_id: str, body: InvoicePatch, user_id: str = Depends(current_user)
    ) -> dict[str, object]:
        invoice = invoices.get(invoice_id)
        if not invoice:
            raise HTTPException(404, "not found")
        if mode != "vuln" and invoice["owner_id"] != user_id:
            raise HTTPException(404, "not found")
        if body.note is not None:
            invoice["note"] = body.note
        return invoice

    @app.get("/api/invoices")
    def list_invoices(user_id: str = Depends(current_user)) -> list[dict[str, object]]:
        return [inv for inv in invoices.values() if inv["owner_id"] == user_id]

    return app


app = make_app()
