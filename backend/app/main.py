"""FastAPI app:案件 CRUD、草稿 PATCH / PDF,pipeline 走 BackgroundTask。"""
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.auth import require_api_key
from app.config import settings
from app.document_check import check_document
from app.models import (
    Case,
    CaseDocument,
    CaseInfo,
    CaseSummary,
    DecisionHeader,
    DOCUMENT_SLOT_LABELS,
    DocumentSlot,
    DraftPatch,
    DraftVersion,
    MAX_DRAFT_VERSIONS,
    OCR_REVIEW_NOTE,
    ScreeningOverride,
    build_input_text,
)
from app.ocr import (
    MIN_TEXT_CHARS,
    OcrFailedError,
    OcrTooManyPagesError,
    OcrUnavailableError,
    get_ocr_client,
    ocr_pdf,
)
from app.pdf_extract import extract_text_quality
from app.pdf_render import build_decision_blocks, render_draft_pdf
from app.text_quality import is_unreadable
from app.reference_data import reference_data_status
from app.review import needs_review
from botocore.exceptions import ClientError, NoCredentialsError
from app.pipeline import check_deadline_from_case, rerun_case, run_case
from app.providers.aws import AWSProvider
from app.providers.base import AIProvider
from app.providers.mock import MockProvider
from app.store import CaseTooLargeError, get_store

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


@app.exception_handler(CaseTooLargeError)
async def case_too_large_handler(_request, exc: CaseTooLargeError):
    """卷證太大是使用者可以理解並處理的輸入問題(改貼摘要、分次處理),不是伺服器錯誤;
    寫入前就擋下並用中文說清楚,勝過讓 boto3 的 ValidationException 把案件打成 status=error。"""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# 憑證類錯誤:處置方式是換一組憑證,不是查伺服器。臨時憑證數小時即過期,裸 500 會讓
# 整個站看起來壞掉而看不出原因,清單頁又是最先被打開的那一頁
_CREDENTIAL_ERROR_CODES = frozenset(
    {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"}
)
_CREDENTIAL_DETAIL = "AWS 憑證無效或已過期,請更新後重試;本機請更新 ~/.aws/credentials,雲端請更新工作負載的憑證來源。"


@app.exception_handler(NoCredentialsError)
async def missing_credentials_handler(_request, _exc: NoCredentialsError):
    return JSONResponse(status_code=503, content={"detail": _CREDENTIAL_DETAIL})


@app.exception_handler(ClientError)
async def aws_client_error_handler(_request, exc: ClientError):
    """只改寫憑證類;其餘照舊往外拋,否則真正的故障會被這層蓋成「請換憑證」。"""
    if exc.response.get("Error", {}).get("Code") in _CREDENTIAL_ERROR_CODES:
        return JSONResponse(status_code=503, content={"detail": _CREDENTIAL_DETAIL})
    raise exc


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
    """一併回報兩份外部對照資料的涵蓋狀態:假日表過期會讓末日順延算不出來,
    這裡的 warning 是唯一會主動講出「該重抓了」的地方(見 reference_data.py)。"""
    status = reference_data_status()
    return {
        "status": "ok",
        "provider": settings.AI_PROVIDER,
        "reference_data": status,
        "warning": status["warning"],
    }


class DocumentInput(NamedTuple):
    text: str
    source: str
    ocr: bool = False


def _ocr_document(label: str, pdf_bytes: bytes) -> DocumentInput:
    """文字層不足的 PDF(掃描件)。mock 模式沒有 OCR,直接說清楚;有 OCR 的模式逐頁抽字,
    任何一頁失敗就整槽拒收——寧可整份退回,也不要交出半份看起來完整的卷證。"""
    try:
        client = get_ocr_client()
    except OcrUnavailableError as exc:
        raise HTTPException(status_code=400, detail=f"{label}疑為掃描件(無可用文字層):{exc}")
    try:
        text = ocr_pdf(pdf_bytes, client)
    except (OcrFailedError, OcrTooManyPagesError) as exc:
        raise HTTPException(status_code=400, detail=f"{label}{exc}")

    stripped = "".join(text.split())
    if len(stripped) < MIN_TEXT_CHARS or is_unreadable(stripped):
        raise HTTPException(
            status_code=400,
            detail=f"{label}逐頁抽字後仍無法辨識,請改用電子檔或直接貼上文字。",
        )
    return DocumentInput(text=text, source="pdf", ocr=True)


async def _read_document_input(
    slot: DocumentSlot, file: Optional[UploadFile], text: Optional[str], *, required: bool = True
) -> DocumentInput:
    """(file, text) 二擇一 -> 純文字 + 來源。兩者皆無或皆空時:必填槽回 400,選填槽回空文字槽;
    PDF 抽不到足量文字層時視為掃描件,依模式走 OCR 或明確拒收,不留下空白案件。"""
    label = DOCUMENT_SLOT_LABELS[slot]
    if file is not None:
        pdf_bytes = await file.read()
        if len(pdf_bytes) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{label}檔案超過 20MB 上限,請改用文字貼上。")
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail=f"{label}檔案讀取失敗,請改用文字貼上。")
        try:
            extracted = extract_text_quality(pdf_bytes)
        except Exception:
            raise HTTPException(status_code=400, detail=f"{label}檔案讀取失敗,請改用文字貼上。")
        if extracted.char_count < MIN_TEXT_CHARS:
            return _ocr_document(label, pdf_bytes)
        return DocumentInput(text=extracted.text, source="pdf")
    if text is not None and text.strip():
        return DocumentInput(text=text, source="text")
    if not required:
        return DocumentInput(text="", source="text")
    raise HTTPException(status_code=400, detail=f"必須提供{label}的 file(PDF)或 text")


def _build_document(slot: DocumentSlot, document_input: DocumentInput) -> CaseDocument:
    check = check_document(slot, document_input.text)
    return CaseDocument(
        slot=slot,
        source=document_input.source,
        text=document_input.text,
        check=check,
        ocr=document_input.ocr,
        review_note=OCR_REVIEW_NOTE if document_input.ocr else "",
    )


@app.post("/api/cases", dependencies=[Depends(require_api_key)])
async def create_case(
    appeal_file: Optional[UploadFile] = File(None),
    appeal_text: Optional[str] = Form(None),
    service_file: Optional[UploadFile] = File(None),
    service_text: Optional[str] = Form(None),
    disposition_file: Optional[UploadFile] = File(None),
    disposition_text: Optional[str] = Form(None),
    answer_file: Optional[UploadFile] = File(None),
    answer_text: Optional[str] = Form(None),
):
    """四份文件各自上傳並確認型態;不在此觸發分析,見 /analyze。
    送達證書選填:觀念通知等案件本無此文書,擋在收案就測不到後續;缺槽由期間計算標記人工確認。
    訴願答辯書同為選填,理由不同:它是原處分機關受理後才送來的,收案當下本來就不會有。"""
    appeal = await _read_document_input("appeal", appeal_file, appeal_text)
    service = await _read_document_input("service", service_file, service_text, required=False)
    disposition = await _read_document_input("disposition", disposition_file, disposition_text)
    answer = await _read_document_input("answer", answer_file, answer_text, required=False)

    documents = {
        "appeal": _build_document("appeal", appeal),
        "service": _build_document("service", service),
        "disposition": _build_document("disposition", disposition),
        "answer": _build_document("answer", answer),
    }
    input_text = build_input_text(documents)
    source = (
        "pdf"
        if "pdf" in (appeal.source, service.source, disposition.source, answer.source)
        else "text"
    )
    appellant_title = appeal.text.strip()[:30] if appeal.source == "text" else (appeal_file.filename or "訴願案件")

    case_id = f"c-{uuid.uuid4().hex[:8]}"
    case = Case(
        case_id=case_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        title=appellant_title,
        source=source,
        input_text=input_text,
        documents=documents,
    )
    store.create(case)

    return {
        "case_id": case_id,
        "documents": {slot: doc.check for slot, doc in documents.items()},
    }


@app.patch("/api/cases/{case_id}/documents/{slot}", dependencies=[Depends(require_api_key)])
async def replace_document(
    case_id: str,
    slot: DocumentSlot,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
):
    """確認結果錯了就整份重傳這一槽,重新跑型態確認;分析中/已完成的案件不可再改文件。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status != "collecting":
        raise HTTPException(status_code=409, detail="案件已開始分析,無法再修改文件")

    document = _build_document(slot, await _read_document_input(slot, file, text))
    documents = {**case.documents, slot: document}
    # 重建 input_text 是必要的——它是 documents 的衍生值,少了這行 F1/程序審查分析用的
    # 仍是重傳前的舊文字。
    store.update(case_id, {"documents": documents, "input_text": build_input_text(documents)})
    return {"documents": {s: d.check for s, d in documents.items()}}


@app.post("/api/cases/{case_id}/analyze", dependencies=[Depends(require_api_key)])
def analyze_case(case_id: str, background_tasks: BackgroundTasks):
    """使用者確認必填三槽文件無誤後按下「開始分析」,才觸發既有的 F1->審查->F2/F3->F4 pipeline。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status != "collecting":
        raise HTTPException(status_code=409, detail="此案件已經開始分析或已完成")

    # 前端擋了未確認的槽,但後端自己也要擋——直接打 API 不能繞過文件確認這一關。
    # matched 為 False 或 None 都不算確認完成,None 尤其不可當「還沒查」跟「已放行」混在一起。
    # 空槽除外:選填槽沒有文件就沒有東西可確認,擋在這裡等於收案放行卻分析不了;
    # 缺這份文書的後果由期間計算標成 review_note,不在此處攔。
    unconfirmed = [
        DOCUMENT_SLOT_LABELS[slot]
        for slot, doc in case.documents.items()
        if doc.check.matched is not True and doc.text.strip()
    ]
    if unconfirmed:
        raise HTTPException(
            status_code=409, detail=f"以下文件尚未確認無誤,無法開始分析:{'、'.join(unconfirmed)}"
        )

    store.update(case_id, {"status": "processing", "current_stage": "f1"})
    background_tasks.add_task(run_case, case_id, store, get_provider())
    return {"ok": True}


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
            needs_review=needs_review(c),
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


def _appended_versions(case: Case, version: DraftVersion) -> dict:
    """草稿版本清單的共同寫法:附一版、超過上限丟最舊,並把「有版本被丟掉」記下來。
    版本歷史是單向成長的欄位,不設上限就是等它某天在背景任務裡撞上單筆 400KB 崩掉。"""
    versions = [*case.draft_versions, version]
    truncated = case.draft_versions_truncated or len(versions) > MAX_DRAFT_VERSIONS
    return {"draft_versions": versions[-MAX_DRAFT_VERSIONS:], "draft_versions_truncated": truncated}


def _version_of(draft) -> DraftVersion:
    return DraftVersion(
        saved_at=datetime.now(timezone.utc).isoformat(),
        fact=draft.fact,
        reason=draft.reason,
        main_text=draft.main_text,
    )


def _same_content(version: DraftVersion, draft) -> bool:
    """已經存過同一份內容就不再重複存一版:重複的版本讀起來像改過但沒改,只會干擾追溯。"""
    return (version.fact, version.reason, version.main_text) == (draft.fact, draft.reason, draft.main_text)


@app.patch("/api/cases/{case_id}/draft", dependencies=[Depends(require_api_key)])
def update_draft(case_id: str, patch: DraftPatch):
    """每次改存一版。定稿後仍然允許修改——定稿只是標記,不鎖。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")
    if patch.base_version is not None and patch.base_version != len(case.draft_versions):
        # 兩個視窗同時改:三種 store 對 Case 都是整包 read-modify-write,不擋就是後送出的
        # 那份無聲蓋掉前一份,而兩邊都以為自己存成功了。回最新內容讓前端提示得出差異。
        raise HTTPException(
            status_code=409,
            detail={
                "message": "這份草稿已被他人更新,請重新載入後再改",
                "current_version": len(case.draft_versions),
                "draft": case.f4.model_dump(),
            },
        )

    updated_f4 = case.f4.model_copy(update=patch.model_dump(exclude={"base_version"}))
    fields = {"f4": updated_f4, **_appended_versions(case, _version_of(updated_f4))}
    store.update(case_id, fields)
    return {"ok": True, "version": len(fields["draft_versions"])}


_STALE_SCREENING_NOTE = "案件資訊經人工修改,程序審查結論尚未依修改後的資料重跑"


@app.patch("/api/cases/{case_id}/f1", dependencies=[Depends(require_api_key)])
def update_case_info(case_id: str, info: CaseInfo) -> Case:
    """承辦人更正 F1 擷取結果。改完不自動重跑程序審查(那要呼叫 LLM,且會蓋掉人工推翻的結論),
    只重算期間並在 screening 標一句「尚未依修改後的資料重跑」——不標的話,畫面上就是一份
    「已審結」但依據已經被改掉的案件。要讓結論跟上,呼叫 /reanalyze。

    期間只有教示條款那一項讀 f1;送達日與提起日仍從文件原文抽取,改 f1 的日期欄不會改變算式。
    """
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="案件分析中,無法修改案件資訊")
    if case.f1 is None:
        raise HTTPException(status_code=409, detail="案件尚未擷取案件資訊,無可修改的內容")

    fields = {
        "f1": info,
        "f1_edited": True,
        "deadline": check_deadline_from_case(case, info),
    }
    if case.screening is not None and _STALE_SCREENING_NOTE not in case.screening.review_note:
        merged = ";".join(n for n in (case.screening.review_note, _STALE_SCREENING_NOTE) if n)
        fields["screening"] = case.screening.model_copy(update={"review_note": merged})
    store.update(case_id, fields)
    return store.get(case_id)


@app.patch("/api/cases/{case_id}/decision-header", dependencies=[Depends(require_api_key)])
def update_decision_header(case_id: str, header: DecisionHeader) -> Case:
    """承辦人自填決定書上系統填不出來的欄位(案號、主任委員與委員名單、決定日期…)。
    整份取代而不逐欄合併:前端送的是整張表單,部分更新會讓「清空某欄」與「沒送某欄」無法區分。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="案件尚未產出草稿,沒有可填的決定書")

    store.update(case_id, {"decision_header": header})
    return store.get(case_id)


@app.patch("/api/cases/{case_id}/screening", dependencies=[Depends(require_api_key)])
def override_screening(case_id: str, override: ScreeningOverride):
    """承辦人推翻程序審查結論。第一次被推翻時把系統原判搬進 screening_system,
    screening 留現行(人工)結論——事後看得出「系統判什麼、人改成什麼」。
    track 跟著翻面,但不自動重跑檢索:自動跑會覆蓋承辦人已編輯的草稿,重跑走 /reanalyze。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.screening is None:
        raise HTTPException(status_code=409, detail="此案件尚無程序審查結論,無從推翻")

    human = case.screening.model_copy(update=override.model_dump())
    fields = {
        "screening": human,
        "track": "admissible" if override.passed else "inadmissible",
    }
    if case.screening_system is None:
        fields["screening_system"] = case.screening
    store.update(case_id, fields)
    return {"ok": True, "track": fields["track"], "overridden": True}


@app.post("/api/cases/{case_id}/reanalyze", dependencies=[Depends(require_api_key)])
def reanalyze_case(case_id: str, background_tasks: BackgroundTasks):
    """重跑。done 與 error 兩種狀態都允許——推翻程序審查之後重跑正是 done 狀態下的
    正常業務操作。契約(含起跑點)見 pipeline.rerun_case。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="此案件正在分析中")
    if case.status == "collecting":
        raise HTTPException(status_code=409, detail="此案件尚未開始分析,請改用 analyze")

    fields = {"status": "processing", "error": None}
    if case.f4 is not None:
        # 重跑會覆蓋 f4,先存一版,否則承辦人編輯過的草稿會被無聲蓋掉
        version = _version_of(case.f4)
        if not (case.draft_versions and _same_content(case.draft_versions[-1], case.f4)):
            fields.update(_appended_versions(case, version))
    store.update(case_id, fields)

    background_tasks.add_task(rerun_case, case_id, store, get_provider())
    return {"ok": True}


def _s3_client():
    import boto3

    return boto3.client("s3", region_name=settings.AWS_REGION)


@app.post("/api/cases/{case_id}/finalize", dependencies=[Depends(require_api_key)])
def finalize_case(case_id: str):
    """定稿:只寫 finalized_at 並落地 PDF。Status 不新增 finalized,承辦人隨時可再改,
    每次改存一版(見 update_draft)——把流程鎖死換不到正確性,只會逼人繞路。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")

    pdf_bytes = render_draft_pdf(case)
    if settings.AI_PROVIDER == "aws":
        key = f"finalized/{case_id}.pdf"
        _s3_client().put_object(
            Bucket=settings.S3_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf"
        )
        location = f"s3://{settings.S3_BUCKET}/{key}"
    else:
        target_dir = Path(settings.FINALIZED_DIR)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{case_id}.pdf"
            target.write_bytes(pdf_bytes)
        except OSError as exc:
            # 落地失敗多半是 FINALIZED_DIR 指到不可寫的位置(容器內尤其容易),
            # 講出是哪個目錄,不要只丟一個 500 讓人猜
            raise HTTPException(
                status_code=500, detail=f"定稿 PDF 無法寫入 {target_dir}:{exc}"
            )
        location = str(target)

    finalized_at = datetime.now(timezone.utc).isoformat()
    store.update(case_id, {"finalized_at": finalized_at})
    return {"finalized_at": finalized_at, "pdf_location": location}


@app.get("/api/cases/{case_id}/decision-skeleton", dependencies=[Depends(require_api_key)])
def get_decision_skeleton(case_id: str):
    """決定書版面骨架;三段本文回 slot,由前端塞可編輯欄位。與 PDF 共用同一份定義。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")
    blocks = build_decision_blocks(case, body_as_slots=True)
    return {"blocks": [{"kind": kind, "text": text} for kind, text in blocks]}


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
