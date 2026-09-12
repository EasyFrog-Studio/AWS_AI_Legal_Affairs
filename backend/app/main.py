"""FastAPI app:案件 CRUD、草稿 PATCH / PDF,pipeline 走 BackgroundTask。"""
import re
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
from app.dates import normalize_case_info_dates, normalize_roc
from app.document_check import check_document
from app.models import (
    DraftTextPatch,
    Case,
    CaseDocument,
    CaseInfo,
    CaseSummary,
    DECISION_HEADER_DATE_FIELDS,
    DOCUMENT_SLOT_LABELS,
    DecisionHeader,
    DocumentSlot,
    DraftResultOverride,
    DraftVersion,
    MAX_DRAFT_VERSIONS,
    OCR_REVIEW_NOTE,
    OPTIONAL_DOCUMENT_SLOTS,
    ReanalyzeRequest,
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
from app.docx_render import render_draft_docx
from app.pdf_render import render_draft_pdf
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
_CREDENTIAL_DETAIL = "AWS 憑證無效或已過期，請更新後重試；本機請更新 ~/.aws/credentials，雲端請更新工作負載的憑證來源。"


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
    filename: str = ""  # 上傳的原始檔名;貼上文字為空字串
    pdf_bytes: bytes = b""  # 上傳的原始 PDF 位元組,落地用;貼上文字為空


def _ocr_document(label: str, pdf_bytes: bytes) -> DocumentInput:
    """文字層不足的 PDF(掃描件)。mock 模式沒有 OCR,直接說清楚;有 OCR 的模式逐頁抽字,
    任何一頁失敗就整槽拒收——寧可整份退回,也不要交出半份看起來完整的卷證。"""
    try:
        client = get_ocr_client()
    except OcrUnavailableError as exc:
        raise HTTPException(status_code=400, detail=f"{label}疑為掃描件（無可用文字層）：{exc}")
    try:
        text = ocr_pdf(pdf_bytes, client)
    except (OcrFailedError, OcrTooManyPagesError) as exc:
        raise HTTPException(status_code=400, detail=f"{label}{exc}")

    stripped = "".join(text.split())
    if len(stripped) < MIN_TEXT_CHARS or is_unreadable(stripped):
        raise HTTPException(
            status_code=400,
            detail=f"{label}逐頁抽字後仍無法辨識，請改用具文字層的電子檔 PDF。",
        )
    return DocumentInput(text=text, source="pdf", ocr=True)


async def _read_document_input(
    slot: DocumentSlot, file: Optional[UploadFile], text: Optional[str]
) -> DocumentInput:
    """(file, text) 二擇一 -> 純文字 + 來源。兩者皆無或皆空時:必填槽回 400,選填槽回空文字槽;
    PDF 抽不到足量文字層時視為掃描件,依模式走 OCR 或明確拒收,不留下空白案件。
    哪些槽選填由 models.OPTIONAL_DOCUMENT_SLOTS 決定,呼叫端不各自帶旗標。"""
    label = DOCUMENT_SLOT_LABELS[slot]
    if file is not None:
        pdf_bytes = await file.read()
        if len(pdf_bytes) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{label}檔案超過 20MB 上限，請壓縮或分拆後再上傳。")
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail=f"{label}檔案讀取失敗，請確認為未加密的 PDF。")
        try:
            extracted = extract_text_quality(pdf_bytes)
        except Exception:
            raise HTTPException(status_code=400, detail=f"{label}檔案讀取失敗，請確認為未加密的 PDF。")
        document = (
            _ocr_document(label, pdf_bytes)
            if extracted.char_count < MIN_TEXT_CHARS
            else DocumentInput(text=extracted.text, source="pdf")
        )
        return document._replace(filename=file.filename or "", pdf_bytes=pdf_bytes)
    if text is not None and text.strip():
        return DocumentInput(text=text, source="text")
    if slot in OPTIONAL_DOCUMENT_SLOTS:
        return DocumentInput(text="", source="text")
    raise HTTPException(status_code=400, detail=f"必須提供{label}的 file（PDF）或 text")


def _build_document(slot: DocumentSlot, document_input: DocumentInput) -> CaseDocument:
    check = check_document(slot, document_input.text)
    return CaseDocument(
        slot=slot,
        source=document_input.source,
        text=document_input.text,
        check=check,
        ocr=document_input.ocr,
        filename=document_input.filename,
        review_note=OCR_REVIEW_NOTE if document_input.ocr else "",
    )


def _land_pdf_if_any(case_id: str, slot: DocumentSlot, document_input: DocumentInput) -> None:
    """上傳的 PDF 位元組落地,供文件確認頁預覽(GET .../file);貼上文字或掃描件無原始 PDF 可落。
    位元組不進 Case/store,寫檔失敗一律往外拋,不可靜默吞掉。"""
    if document_input.source != "pdf" or not document_input.pdf_bytes:
        return
    if settings.AI_PROVIDER == "aws":
        _s3_client().put_object(
            Bucket=settings.S3_BUCKET,
            Key=f"cases/{case_id}/{slot}.pdf",
            Body=document_input.pdf_bytes,
            ContentType="application/pdf",
        )
        return
    target_dir = Path(settings.CASE_FILES_DIR) / case_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{slot}.pdf").write_bytes(document_input.pdf_bytes)


# 案號會落進 finalized/{case_id}.pdf 的 S3 key 與本機路徑,故限白名單;\w 含中日韓字,「114年訴字第0123號」可用
_CASE_ID_RE = re.compile(r"^[\w-]{1,64}\Z")
# 這些名字在 Windows 是裝置而非檔案,寫 CON.pdf 會寫進主控台;非 aws 模式的 FINALIZED_DIR 就在本機
_WINDOWS_DEVICE_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])\Z", re.IGNORECASE)


def _case_id_error(status_code: int, message: str) -> HTTPException:
    """標成 case_id 欄位的錯誤,前端據此判斷要不要附檔案格式提示,不必比對文案。"""
    return HTTPException(status_code=status_code, detail={"message": message, "field": "case_id"})


def _resolve_case_id(raw: Optional[str]) -> str:
    case_id = (raw or "").strip()
    if not case_id:
        return f"c-{uuid.uuid4().hex[:8]}"
    if not _CASE_ID_RE.match(case_id) or _WINDOWS_DEVICE_RE.match(case_id):
        raise _case_id_error(400, "案號僅接受中英文、數字、底線與連字號，長度 64 字以內")
    # store.create() 三種實作皆為 upsert,重號放行就是無聲覆蓋掉同號舊案;
    # 這裡是 check-then-act,靠的是單 task 單 process 且本函式到 create 之間沒有 await,資料層本身無此保證
    if store.get(case_id) is not None:
        raise _case_id_error(409, f"案號 {case_id} 已存在，請換一個或留白由系統產生")
    return case_id


@app.post("/api/cases", dependencies=[Depends(require_api_key)])
async def create_case(
    background_tasks: BackgroundTasks,
    case_id: Optional[str] = Form(None),
    appeal_file: Optional[UploadFile] = File(None),
    appeal_text: Optional[str] = Form(None),
    service_file: Optional[UploadFile] = File(None),
    service_text: Optional[str] = Form(None),
    disposition_file: Optional[UploadFile] = File(None),
    disposition_text: Optional[str] = Form(None),
    answer_file: Optional[UploadFile] = File(None),
    answer_text: Optional[str] = Form(None),
):
    """四份文件各自上傳並確認型態,建案後立即起跑整條 pipeline——不再等待「開始分析」。
    送達證書選填:觀念通知等案件本無此文書,擋在收案就測不到後續;缺槽時期間不予計算,
    一律視為未逾期(見 pipeline.check_deadline_from_case)。
    訴願答辯書必填:機關的答辯是實體審理的另一造主張,缺了只聽得到訴願人一方。"""
    appeal = await _read_document_input("appeal", appeal_file, appeal_text)
    service = await _read_document_input("service", service_file, service_text)
    disposition = await _read_document_input("disposition", disposition_file, disposition_text)
    answer = await _read_document_input("answer", answer_file, answer_text)

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

    case_id = _resolve_case_id(case_id)
    case = Case(
        case_id=case_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        title=appellant_title,
        source=source,
        input_text=input_text,
        documents=documents,
    )
    store.create(case)
    for slot, document_input in (("appeal", appeal), ("service", service), ("disposition", disposition), ("answer", answer)):
        _land_pdf_if_any(case_id, slot, document_input)
    background_tasks.add_task(run_case, case_id, store, get_provider())

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
    """確認結果錯了就整份重傳這一槽,重新跑型態確認並落地新的 PDF;分析中的案件不可再改文件。
    不觸發分析——修正型態或補件之後要看到新結果,呼叫 /reanalyze。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="分析進行中，請稍後再重傳")

    document_input = await _read_document_input(slot, file, text)
    document = _build_document(slot, document_input)
    _land_pdf_if_any(case_id, slot, document_input)
    documents = {**case.documents, slot: document}
    # 重建 input_text 是必要的——它是 documents 的衍生值,少了這行 F1/程序審查分析用的
    # 仍是重傳前的舊文字。
    store.update(case_id, {"documents": documents, "input_text": build_input_text(documents)})
    return {"documents": {s: d.check for s, d in documents.items()}}


@app.get("/api/cases/{case_id}/documents/{slot}/file", dependencies=[Depends(require_api_key)])
def get_document_file(case_id: str, slot: DocumentSlot):
    """承辦人在文件確認頁點檔名預覽當初上傳的原始 PDF。貼上文字的槽沒有原始檔可看,回 404;
    aws 模式直接串流 S3 物件回傳,不用 302——前端一律走同一條帶金鑰的 fetch。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    document = case.documents.get(slot)
    if document is None or document.source != "pdf":
        raise HTTPException(status_code=404, detail="此槽無原始 PDF 可預覽")

    filename = urllib.parse.quote(document.filename or f"{slot}.pdf")
    if settings.AI_PROVIDER == "aws":
        try:
            obj = _s3_client().get_object(Bucket=settings.S3_BUCKET, Key=f"cases/{case_id}/{slot}.pdf")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise HTTPException(status_code=404, detail="檔案不存在")
            raise
        pdf_bytes = obj["Body"].read()
    else:
        path = Path(settings.CASE_FILES_DIR) / case_id / f"{slot}.pdf"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="檔案不存在")
        pdf_bytes = path.read_bytes()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
    )


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
            result=c.f4.draft_type if c.f4 else None,
            documents_failed=any(doc.check.matched is False for doc in c.documents.values()),
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

    # 參考見解的存檔 PDF 不能當文字塞進 overlay;回 file 讓前端改走下載後開新分頁那條路
    if key.startswith("reference/"):
        return {"file": key}

    # mock 模式:回傳本地前處理輸出的 markdown 內容(找不到則回提示文字)
    # key 為外部輸入,resolve 後必須仍在 data/output/ 內,防路徑穿越
    base = (_REPO_ROOT.parent / "data" / "output").resolve()
    local_path = (base / key).resolve()
    if local_path.is_relative_to(base) and local_path.is_file():
        return {"text": local_path.read_text(encoding="utf-8")}
    return {"text": f"[mock 模式] 本地找不到對應檔案：{key}"}


# 爬蟲語料的參考資料 PDF;容器內由 compose 掛在這裡,掛不上就每一份都回 404 而不是靜默給空白
_REFERENCE_DIR = Path("/data/reference")


@app.get("/api/source/file", dependencies=[Depends(require_api_key)])
def get_source_file(key: str):
    """參考見解的存檔 PDF。key 由 providers.archived_source_key 產生(`reference/<類別>/<檔名>.pdf`),
    resolve 後必須仍在存放目錄內——那個值來自語料 metadata,不是使用者輸入,但它會被組成路徑。"""
    if not key.startswith("reference/"):
        raise HTTPException(status_code=404, detail="source not found")
    base = _REFERENCE_DIR.resolve()
    path = (base / key[len("reference/") :]).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status_code=404, detail="source not found")
    filename = urllib.parse.quote(path.name)
    return Response(
        content=path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
    )


def _appended_versions(case: Case, version: DraftVersion) -> dict:
    """草稿版本清單的共同寫法:附一版、超過上限丟最舊,並把「有版本被丟掉」記下來。
    版本歷史是單向成長的欄位,不設上限就是等它某天在背景任務裡撞上單筆 400KB 崩掉。"""
    versions = [*case.draft_versions, version]
    truncated = case.draft_versions_truncated or len(versions) > MAX_DRAFT_VERSIONS
    return {"draft_versions": versions[-MAX_DRAFT_VERSIONS:], "draft_versions_truncated": truncated}


def _version_of(text: str) -> DraftVersion:
    return DraftVersion(saved_at=datetime.now(timezone.utc).isoformat(), text=text)


def _same_content(version: DraftVersion, text: str) -> bool:
    """已經存過同一份內容就不再重複存一版:重複的版本讀起來像改過但沒改,只會干擾追溯。"""
    return version.text == text


@app.patch("/api/cases/{case_id}/f1", dependencies=[Depends(require_api_key)])
def update_case_info(case_id: str, info: CaseInfo) -> Case:
    """承辦人更正 F1 擷取結果。改完不自動重跑程序審查(那要呼叫 LLM,且會蓋掉人工推翻的結論),
    只重算期間;下游是否已經過時由 computed field f1_stale 表達(比對 screening_input_f1),
    不再另外在 screening.review_note 塞一句。要讓結論跟上,呼叫 /reanalyze。

    期間重算時,承辦人改過的送達時間與機關收文日期取代文件原文抽到的值(見 pipeline._edited_deadline_dates)。
    """
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="案件分析中，無法修改案件資訊")
    if case.f1 is None:
        raise HTTPException(status_code=409, detail="案件尚未擷取案件資訊，無可修改的內容")

    info = normalize_case_info_dates(info)
    fields = {
        "f1": info,
        "f1_edited": True,
        "deadline": check_deadline_from_case(case, info),
    }
    # 只在第一次修改時留快照:第二次改若覆蓋掉,第一次改過的欄位就不再標記為已修改
    if case.f1_system is None:
        fields["f1_system"] = case.f1
    store.update(case_id, fields)
    return store.get(case_id)


@app.patch("/api/cases/{case_id}/draft-text", dependencies=[Depends(require_api_key)])
def update_draft_text(case_id: str, patch: DraftTextPatch):
    """決定書全文的修改,每次存一版;附帶承辦人維護的引用法條清單。定稿後仍然允許修改——定稿只是標記,不鎖。"""
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
                "message": "這份草稿已被他人更新，請重新載入後再改",
                "current_version": len(case.draft_versions),
                "text": case.draft_plain_text,
            },
        )

    fields = {"draft_plain_text": patch.text, **_appended_versions(case, _version_of(patch.text))}
    if patch.cited_laws is not None:
        # 只動 cited_laws,不碰 f4_system:那個快照是「決定結果被改過」的憑據,不是法條清單的
        fields["f4"] = case.f4.model_copy(update={"cited_laws": patch.cited_laws})
    store.update(case_id, fields)
    return {"ok": True, "version": len(fields["draft_versions"])}


@app.patch("/api/cases/{case_id}/screening", dependencies=[Depends(require_api_key)])
def override_screening(case_id: str, override: ScreeningOverride):
    """承辦人推翻程序審查結論。第一次被推翻時把系統原判搬進 screening_system,
    screening 留現行(人工)結論——事後看得出「系統判什麼、人改成什麼」。
    track 跟著翻面,但不自動重跑檢索:自動跑會覆蓋承辦人已編輯的草稿,重跑走 /reanalyze。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="案件分析中，無法修改程序審查結論")
    if case.screening is None:
        raise HTTPException(status_code=409, detail="此案件尚無程序審查結論，無從推翻")

    human = case.screening.model_copy(update=override.model_dump())
    fields = {
        "screening": human,
        "track": "admissible" if override.passed else "inadmissible",
    }
    if case.screening_system is None:
        fields["screening_system"] = case.screening
    store.update(case_id, fields)
    return {"ok": True, "track": fields["track"], "overridden": True}


@app.patch("/api/cases/{case_id}/draft/result", dependencies=[Depends(require_api_key)])
def update_draft_result(case_id: str, override: DraftResultOverride):
    """承辦人改決定結果(五值)。只改 f4.draft_type,不改 track、不改 screening、不改全文——
    主文要不要跟著改由承辦人自己在下方全文裡改。第一次被改時把系統原判整份 f4 存進 f4_system。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="案件分析中，無法修改決定結果")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿，無可修改的決定結果")

    human = case.f4.model_copy(update={"draft_type": override.draft_type})
    fields = {"f4": human}
    if case.f4_system is None:
        fields["f4_system"] = case.f4
    store.update(case_id, fields)
    return {"ok": True, "draft_type": human.draft_type, "overridden": True}


@app.post("/api/cases/{case_id}/reanalyze", dependencies=[Depends(require_api_key)])
def reanalyze_case(case_id: str, request: ReanalyzeRequest, background_tasks: BackgroundTasks):
    """重跑,起跑點由前端明確指定。done 與 error 兩種狀態都允許——推翻程序審查之後重跑
    正是 done 狀態下的正常業務操作。前面的階段只影響後面的:from=screening 保留 f1,
    from=f2 保留 f1 與 screening。契約見 specs/審理歷程線性重跑與全欄位擷取.md §1.2,
    實際起跑由 pipeline.rerun_case 執行。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="此案件正在分析中")
    if request.from_stage in ("screening", "f2") and case.f1 is None:
        raise HTTPException(status_code=409, detail="案件尚未擷取案件資訊，無法自此重跑")
    if request.from_stage == "f2" and case.screening is None:
        raise HTTPException(status_code=409, detail="案件尚無程序審查結論，無法自參考依據重跑")

    # 重跑前一律先存一版:重跑會重新產生全文,不存的話承辦人編輯過的草稿會被無聲蓋掉
    fields = {"status": "processing", "error": None}
    if case.f4 is not None:
        text = case.draft_plain_text
        if not (case.draft_versions and _same_content(case.draft_versions[-1], text)):
            fields.update(_appended_versions(case, _version_of(text)))

    if request.from_stage == "f1":
        # 整條重跑:新結果不是承辦人改的,留著舊快照會讓整份都標成已修改
        fields.update(
            {
                "f1_system": None,
                "f1_edited": False,
                "screening_system": None,
                "f4_system": None,
                "screening_input_f1": None,
                "retrieval_input_screening": None,
                "current_stage": "f1",
            }
        )
    elif request.from_stage == "screening":
        # 保留 f1 與 f1_system,自程序審查起跑
        fields.update(
            {
                "screening_system": None,
                "f4_system": None,
                "retrieval_input_screening": None,
                "current_stage": "screening",
            }
        )
    else:  # f2:保留 f1/f1_system/screening/screening_system,自參考依據起跑
        fields.update(
            {
                "f4_system": None,
                "current_stage": "f2" if case.screening.passed else "f2_refs",
            }
        )

    store.update(case_id, fields)
    background_tasks.add_task(rerun_case, case_id, store, get_provider(), request.from_stage)
    return {"ok": True}


@app.patch("/api/cases/{case_id}/decision-header", dependencies=[Depends(require_api_key)])
def update_decision_header(case_id: str, header: DecisionHeader):
    """決定書表頭與結尾的結構化欄位。F4 落地時已寫入預設值,這裡是承辦人之後的修改;
    案號等欄位允許被改成空白——表頭不是唯讀的衍生資料。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status == "processing":
        raise HTTPException(status_code=409, detail="案件分析中，無法修改決定書表頭")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿，無可修改的表頭")

    updates = {}
    for field in DECISION_HEADER_DATE_FIELDS:
        raw = getattr(header, field)
        normalized = normalize_roc(raw)
        if normalized is not None and normalized != raw:
            updates[field] = normalized
    normalized_header = header.model_copy(update=updates) if updates else header

    store.update(case_id, {"decision_header": normalized_header})
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
                status_code=500, detail=f"定稿 PDF 無法寫入 {target_dir}：{exc}"
            )
        location = str(target)

    finalized_at = datetime.now(timezone.utc).isoformat()
    store.update(case_id, {"finalized_at": finalized_at})
    return {"finalized_at": finalized_at, "pdf_location": location}


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


@app.get("/api/cases/{case_id}/draft.docx", dependencies=[Depends(require_api_key)])
def get_draft_docx(case_id: str):
    """同一份草稿的 Word 版。與 draft.pdf 共用 build_decision_blocks,體例不會兩邊走鐘。"""
    case = store.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.f4 is None:
        raise HTTPException(status_code=409, detail="此案件尚無草稿")
    filename = urllib.parse.quote(f"決定書草稿_{case_id}.docx")
    return Response(
        content=render_draft_docx(case),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
