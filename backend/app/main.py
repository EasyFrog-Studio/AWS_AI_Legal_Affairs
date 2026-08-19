"""FastAPI app(見 DECISIONS.md「Backend API 契約」節,路由與 schema 逐字照做)。"""
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.auth import require_api_key
from app.config import settings
from app.models import Case, CaseSummary, DraftPatch
from app.pdf_extract import extract_text
from app.pdf_render import render_draft_pdf
from app.pipeline import run_case
from app.providers.aws import AWSProvider
from app.providers.base import AIProvider
from app.providers.mock import MockProvider
from app.store import get_store

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="訴願案件輔助審查系統")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # 同源請求瀏覽器不受 CORS 限制,此處僅需列 dev cross-origin 來源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = get_store()


_provider: Optional[AIProvider] = None


def get_provider() -> AIProvider:
    # boto3 client 建立成本不低,以模組層單例重用(與 get_store 一致)
    global _provider
    if _provider is None:
        if settings.AI_PROVIDER == "aws":
            _provider = AWSProvider()
        elif settings.AI_PROVIDER == "local":
            from app.providers.local import LocalProvider

            _provider = LocalProvider()
        else:
            _provider = MockProvider()
    return _provider


@app.get("/api/health")
def health():
    return {"status": "ok", "provider": settings.AI_PROVIDER}


@app.post("/api/cases", dependencies=[Depends(require_api_key)])
async def create_case(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
):
    if file is not None:
        pdf_bytes = await file.read()
        if len(pdf_bytes) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="檔案超過 20MB 上限,請改用文字貼上。")
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="檔案讀取失敗,請改用文字貼上。")
        try:
            input_text = extract_text(pdf_bytes)
        except Exception:
            raise HTTPException(status_code=400, detail="檔案讀取失敗,請改用文字貼上。")
        source = "pdf"
        title = file.filename or "PDF案件"
    elif text is not None and text.strip():
        input_text = text
        source = "text"
        title = text.strip()[:30]
    else:
        raise HTTPException(status_code=400, detail="必須提供 file(PDF)或 text")

    case_id = f"c-{uuid.uuid4().hex[:8]}"
    case = Case(
        case_id=case_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        title=title,
        source=source,
        input_text=input_text,
    )
    store.create(case)

    background_tasks.add_task(run_case, case_id, store, get_provider())
    return {"case_id": case_id}


@app.get("/api/cases", dependencies=[Depends(require_api_key)])
def list_cases() -> list[CaseSummary]:
    cases = sorted(store.list_cases(), key=lambda c: c.created_at, reverse=True)
    return [
        CaseSummary(
            case_id=c.case_id,
            created_at=c.created_at,
            title=c.title,
            status=c.status,
            track=c.track,
            current_stage=c.current_stage,
            case_type=c.f1.case_type if c.f1 else None,
        )
        for c in cases
    ]


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_api_key)])
def get_case(case_id: str) -> Case:
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


@app.get("/api/source", dependencies=[Depends(require_api_key)])
def get_source(key: str):
    if settings.AI_PROVIDER == "aws":
        import boto3

        s3 = boto3.client("s3", region_name=settings.AWS_REGION)
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": settings.S3_BUCKET, "Key": key}, ExpiresIn=3600
        )
        return {"url": url}

    # mock 模式:回傳本地前處理輸出的 markdown 內容(找不到則回提示文字)
    # key 為外部輸入,resolve 後必須仍在 data/output/ 內,防路徑穿越
    base = (_REPO_ROOT.parent / "data" / "output").resolve()
    local_path = (base / key).resolve()
    if local_path.is_relative_to(base) and local_path.is_file():
        return {"text": local_path.read_text(encoding="utf-8")}
    return {"text": f"[mock 模式] 本地找不到對應檔案:{key}"}


@app.patch("/api/cases/{case_id}/draft", dependencies=[Depends(require_api_key)])
def update_draft(case_id: str, patch: DraftPatch):
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")
    updated_f4 = case.f4.model_copy(update=patch.model_dump())
    store.update(case_id, {"f4": updated_f4})
    return {"ok": True}


@app.get("/api/cases/{case_id}/draft.pdf", dependencies=[Depends(require_api_key)])
def get_draft_pdf(case_id: str):
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")
    pdf_bytes = render_draft_pdf(case)
    filename = urllib.parse.quote(f"決定書草稿_{case_id}.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ---------- 掛載前端靜態檔(若存在),SPA fallback(/api/* 除外) ----------
if _STATIC_DIR.is_dir():

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        # full_path 為外部輸入(含 %2e%2e 編碼繞過),resolve 後必須仍在 static/ 內
        static_root = _STATIC_DIR.resolve()
        candidate = (_STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_relative_to(static_root) and candidate.is_file():
            return FileResponse(candidate)
        index_file = _STATIC_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        raise HTTPException(status_code=404)
