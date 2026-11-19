import secrets
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .service import Conflict, Inbox, NotFound


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=524288)
    filename: str = Field(max_length=150)
    permission_note: str = Field(min_length=1, max_length=300)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)
    reviewer: str = Field(min_length=2, max_length=80)
    action: Literal["save", "approve", "reject"]
    draft: str = Field(min_length=1, max_length=5000)
    category: Literal["billing", "support", "sales"] | None = None


def create_app(db_path="data/inbox.sqlite3"):
    inbox = Inbox(db_path)
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="Inbox Triage", description="Local draft review only; no outbound delivery")
    app.state.inbox = inbox

    def require_token(x_token: Annotated[str | None, Header()] = None):
        if not secrets.compare_digest(x_token or "", token):
            raise HTTPException(403, "Refresh the local page before making changes")

    def invoke(operation):
        try:
            return operation()
        except NotFound as error:
            raise HTTPException(404, str(error)) from error
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/", response_class=HTMLResponse)
    def index():
        content = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
        return HTMLResponse(content.replace("__TOKEN__", token), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/messages")
    def listing(limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
        return inbox.listing(limit, offset)

    @app.get("/api/messages/{identifier}/events")
    def history(identifier: int):
        return invoke(lambda: inbox.history(identifier))

    @app.post("/api/import", dependencies=[Depends(require_token)])
    def import_messages(payload: ImportRequest):
        return invoke(lambda: inbox.import_messages(payload.content, Path(payload.filename).suffix, payload.permission_note))

    @app.post("/api/demo", dependencies=[Depends(require_token)])
    def demo():
        return inbox.import_messages(Path(__file__).with_name("sample_messages.json").read_text(encoding="utf-8"),
                                     ".json", "Author-generated synthetic demonstration")

    @app.post("/api/messages/{identifier}/review", dependencies=[Depends(require_token)])
    def review(identifier: int, payload: ReviewRequest):
        return invoke(lambda: inbox.review(identifier, **payload.model_dump()))

    @app.get("/api/export")
    def export():
        return Response("\ufeff" + inbox.export(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="approved-drafts.csv"'})

    @app.get("/health")
    def health():
        return {"status": "ok", "messages": inbox.listing(1)["total"], "engine": "rules-v1", "outreach_enabled": False}

    return app
