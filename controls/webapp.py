"""A fixture 'deployed web app' with a real signup/login UI and a JSON API, for B2/B3.

The control twin (`controls/app.py`) is a JSON API; this one is a real HTML app the
browser can drive: a landing page, signup and login forms that set a session cookie,
and a dashboard that only renders when signed in. The dashboard fetches its data from
a small JSON API (`/api/invoices`), so driving it produces the XHR/fetch traffic that
B3's resource discovery learns from.

`obfuscated=True` renders the fields without the usual `type`/`name`/`id` hooks, so
the heuristics cannot find them — the vision-fallback path. Ground truth for both.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<main>
{body}
</main>
</body></html>
"""

_LANDING = """
<h1>Invoice App</h1>
<p>A tiny app to test account flows.</p>
<p><a href="/signup">Sign up</a> · <a href="/login">Log in</a></p>
"""

_SIGNUP = """
<h1>Create your account</h1>
<form method="post" action="/signup">
  <label for="name">Name</label>
  <input id="name" name="name" type="text" placeholder="Your name" />
  <label for="email">Email</label>
  <input id="email" name="email" type="email" placeholder="you@example.com" />
  <label for="password">Password</label>
  <input id="password" name="password" type="password" placeholder="Password" />
  <button type="submit">Create account</button>
</form>
<p>Already have an account? <a href="/login">Log in</a></p>
"""

_LOGIN = """
<h1>Log in</h1>
<form method="post" action="/login">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" placeholder="you@example.com" />
  <label for="password">Password</label>
  <input id="password" name="password" type="password" placeholder="Password" />
  <button type="submit">Log in</button>
</form>
"""

# No type/name/id hooks - the heuristics must fall through to the vision fallback.
_OBFUSCATED_SIGNUP = """
<h1>Create your account</h1>
<form method="post" action="/signup">
  <label for="f1">Name</label>
  <input id="f1" placeholder="Your name" />
  <label for="f2">Email address</label>
  <input id="f2" placeholder="you@example.com" />
  <label for="f3">Choose a password</label>
  <input id="f3" type="password" placeholder="Password" />
  <button type="submit">Create account</button>
</form>
"""

# The dashboard loads its data over the network, like a real single-page app.
_DASHBOARD_SCRIPT = """
<div id="invoices">Loading…</div>
<script>
(async () => {
  const box = document.getElementById('invoices');
  try {
    const list = await (await fetch('/api/invoices')).json();
    box.innerHTML = '';
    for (const inv of list.invoices) {
      const one = await (await fetch('/api/invoices/' + inv.id)).json();
      const p = document.createElement('p');
      p.textContent = inv.id + ' · ' + one.amount;
      box.appendChild(p);
    }
  } catch (err) {
    box.textContent = '';
  }
})();
</script>
"""

# Like the dashboard above, but only the collection is fetched - a record appears only
# if the scan derives its path from an id in the listing (no detail call in traffic).
_DASHBOARD_LIST_ONLY_SCRIPT = """
<div id="invoices">Loading…</div>
<script>
(async () => {
  const box = document.getElementById('invoices');
  try {
    const list = await (await fetch('/api/invoices')).json();
    box.innerHTML = '';
    for (const inv of list.invoices) {
      const p = document.createElement('p');
      p.textContent = inv.id;
      box.appendChild(p);
    }
  } catch (err) {
    box.textContent = '';
  }
})();
</script>
"""


# A create form, so a scan can make a record of its own and then attack *that*
# (never the user's existing data). The dashboard reloads after the POST.
_CREATE_UI = """
<form id="create">
  <label for="note">Note</label>
  <input id="note" name="note" type="text" placeholder="What is this invoice for?" />
  <button type="submit">Add invoice</button>
</form>
<script>
(() => {
  const form = document.getElementById('create');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const note = form.elements.note.value;
    await fetch('/api/invoices', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({note}),
    });
    location.reload();
  });
})();
</script>
"""


def make_webapp(
    *,
    obfuscated: bool = False,
    vulnerable: bool = False,
    list_only: bool = False,
    create_ui: bool = True,
) -> FastAPI:
    app = FastAPI(title="fixture-webapp")
    users: dict[str, str] = {}
    invoices: dict[str, list[dict]] = {}

    def _page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(_PAGE.format(title=title, body=body))

    def _email(request: Request) -> str | None:
        session = request.cookies.get("session")
        return session.removeprefix("user:") if session else None

    def _signed_in(request: Request) -> Response:
        email = _email(request)
        if email is None:
            return RedirectResponse("/login", status_code=303)
        return _page(
            "Dashboard",
            f"<h1>Welcome, {email}</h1><p>You are signed in.</p>"
            '<form method="post" action="/logout"><button type="submit">Sign out</button></form>'
            + (_CREATE_UI if create_ui else "")
            + (_DASHBOARD_LIST_ONLY_SCRIPT if list_only else _DASHBOARD_SCRIPT),
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return _page("Invoice App", _LANDING)

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form() -> HTMLResponse:
        return _page("Sign up", _OBFUSCATED_SIGNUP if obfuscated else _SIGNUP)

    @app.post("/signup")
    def signup(name: str = Form(""), email: str = Form(""), password: str = Form("")) -> Response:
        users[email] = password
        invoices[email] = [
            {
                "id": f"inv-{uuid.uuid4().hex[:8]}",
                "amount": 42.0,
                "note": f"vrfr-{uuid.uuid4().hex[:12]}",
            }
        ]
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("session", f"user:{email}", httponly=True)
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_form() -> HTMLResponse:
        return _page("Log in", _LOGIN)

    @app.post("/login")
    def login(email: str = Form(""), password: str = Form("")) -> Response:
        if users.get(email) != password:
            return _page("Log in", "<h1>Wrong email or password</h1>" + _LOGIN)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("session", f"user:{email}", httponly=True)
        return response

    @app.get("/dashboard")
    def dashboard(request: Request) -> Response:
        return _signed_in(request)

    @app.get("/api/invoices")
    def list_invoices(request: Request) -> Response:
        email = _email(request)
        if email is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"invoices": invoices.get(email, [])})

    @app.post("/api/invoices")
    async def create_invoice(request: Request) -> Response:
        email = _email(request)
        if email is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        record = {
            "id": f"inv-{uuid.uuid4().hex[:8]}",
            "amount": 42.0,
            "note": str(body.get("note", "")),
        }
        invoices.setdefault(email, []).append(record)
        return JSONResponse(record, status_code=201)

    @app.patch("/api/invoices/{invoice_id}")
    async def update_invoice(invoice_id: str, request: Request) -> Response:
        email = _email(request)
        if email is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if vulnerable:
            pool = [invoice for group in invoices.values() for invoice in group]
        else:
            pool = invoices.get(email, [])
        for invoice in pool:
            if invoice["id"] != invoice_id:
                continue
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if "note" in body:
                invoice["note"] = str(body["note"])
            return JSONResponse(invoice)
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.get("/api/invoices/{invoice_id}")
    def get_invoice(invoice_id: str, request: Request) -> Response:
        email = _email(request)
        if email is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # `vulnerable` models broken object-level authorization on the record route:
        # any signed-in user can fetch any invoice by id (the collection stays scoped).
        if vulnerable:
            pool = [invoice for group in invoices.values() for invoice in group]
        else:
            pool = invoices.get(email, [])
        for invoice in pool:
            if invoice["id"] == invoice_id:
                return JSONResponse(invoice)
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("session")
        return response

    return app
