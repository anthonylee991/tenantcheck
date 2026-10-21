"""A fixture 'deployed frontend' for the URL-in flow.

Looks like a real Vite-style app: an HTML shell plus a JS bundle that carries the
Supabase URL and public key (exactly what Lovable/Bolt/Base44 ship). The bench
points it at the throwaway Supabase so the whole URL-in path can be measured
without touching the internet.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

INDEX = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Invoice App</title>
    <link rel="modulepreload" href="/assets/index-abc123.js" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/assets/index-abc123.js"></script>
  </body>
</html>
"""


def make_frontend(
    supabase_url: str,
    anon_key: str,
    *,
    verification_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="fixture-frontend")
    bundle = (
        f'const supabaseUrl="{supabase_url}";'
        f'const supabaseKey="{anon_key}";'
        "export const client = createClient(supabaseUrl, supabaseKey);"
    )
    meta = (
        f'<meta name="tenantcheck-site-verification" content="{verification_token}" />'
        if verification_token
        else ""
    )
    index_html = INDEX.replace("</head>", f"    {meta}\n  </head>" if meta else "</head>")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return index_html

    @app.get("/assets/index-abc123.js")
    def js() -> Response:
        return Response(bundle, media_type="application/javascript")

    @app.get("/.well-known/tenantcheck-verification.txt")
    def well_known() -> Response:
        if verification_token is None:
            return Response(status_code=404)
        return Response(verification_token, media_type="text/plain")

    return app
