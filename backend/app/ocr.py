"""掃描件逐頁抽字。aws 走 Bedrock 多模態、local 走 ollama 多模態,兩者共用分頁與接回邏輯。

不用 Amazon Textract:官方 quota 文件(docs.aws.amazon.com/textract/latest/dg/limits-document.html)
明載文字偵測支援的語言只有英法德義葡西六種,不含中文,亦不支援中日文常見的直書排列——
繁體中文卷宗它讀不出來,做非同步 job 流程也一樣讀不出來。

逐頁同步呼叫,不需要 job 輪詢也不需要 SNS/SQS,POST /analyze 的請求-回應流程不必改。
"""
import base64
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

import fitz  # PyMuPDF

from app.config import settings

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MIN_TEXT_CHARS = 50  # 抽出的文字少於此字數即視為無文字層(掃描件),與可讀比例是兩個獨立門檻
MAX_OCR_PAGES = 20  # 逐頁同步呼叫,頁數要有明確上限;超過就拒收並說明,不默默跑很久
_RENDER_DPI = 200  # 公文書字級在 200dpi 下足以辨識,再高只是把圖變大、把呼叫變慢
_MAX_ATTEMPTS = 3  # 暫時性的 throttling 用重試處理;重試完仍失敗就是整槽拒收
_BACKOFF_BASE_SECONDS = 1.0


class OcrUnavailableError(Exception):
    """本模式未接 OCR(mock)。要講清楚,不能讓使用者拿到一個空白案件。"""


class OcrTooManyPagesError(Exception):
    """頁數超過上限,整份不送。"""


class OcrFailedError(Exception):
    """某一頁重試後仍失敗。整槽拒收,不留半份——部分文字會讓下游看起來成功。"""


class OcrClient(Protocol):
    def extract_page(self, image: bytes) -> str: ...


def page_images(pdf_bytes: bytes, dpi: int = _RENDER_DPI) -> list[bytes]:
    """每頁 render 成一張 PNG。"""
    images: list[bytes] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            images.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return images


def ocr_pdf(pdf_bytes: bytes, client: OcrClient, sleep: Callable[[float], None] = time.sleep) -> str:
    """逐頁抽字後接回。任何一頁重試後仍失敗即整份拋錯,不回傳部分結果:
    缺掉的那一頁很可能正是載有送達時間或教示條款的那一頁,而抽取層拿到剩下幾頁的完整句子
    不會回報「抽不到」,只會算出一個沒有人知道是錯的日期。"""
    images = page_images(pdf_bytes)
    if len(images) > MAX_OCR_PAGES:
        raise OcrTooManyPagesError(
            f"文件共 {len(images)} 頁,超過逐頁抽字上限 {MAX_OCR_PAGES} 頁,請改用電子檔或分次處理。"
        )

    texts: list[str] = []
    for page_no, image in enumerate(images, start=1):
        texts.append(_extract_with_retry(client, image, page_no, sleep))
    return "\n".join(texts)


def _extract_with_retry(
    client: OcrClient, image: bytes, page_no: int, sleep: Callable[[float], None]
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return client.extract_page(image)
        except Exception as exc:  # noqa: BLE001 - throttling/逾時的例外型別依 provider 而異
            last_error = exc
            if attempt < _MAX_ATTEMPTS - 1:
                sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
    raise OcrFailedError(
        f"第{page_no}頁抽字失敗({last_error}),請改用電子檔或直接貼上文字。"
    )


class BedrockOcrClient:
    """Bedrock converse 的 image block 逐頁抽字。"""

    def __init__(self, bedrock_runtime=None) -> None:
        if bedrock_runtime is None:
            import boto3

            bedrock_runtime = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        self._brt = bedrock_runtime
        self._prompt = (_PROMPTS_DIR / "ocr_page.txt").read_text(encoding="utf-8")

    def extract_page(self, image: bytes) -> str:
        resp = self._brt.converse(
            modelId=settings.BEDROCK_MODEL_ID,
            system=[{"text": self._prompt}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": "png", "source": {"bytes": image}}},
                        {"text": "請逐字轉錄這一頁。"},
                    ],
                }
            ],
            inferenceConfig={"temperature": 0},  # 轉錄不要創意,同一頁跑兩次要得到同一份文字
        )
        blocks = resp["output"]["message"]["content"]
        return "\n".join(block["text"] for block in blocks if "text" in block)


class OllamaOcrClient:
    """ollama /api/chat 的 images(base64)逐頁抽字,讓 OCR 這條路在無 AWS 憑證下也量得到。"""

    def __init__(self, http_client=None) -> None:
        if http_client is None:
            import httpx

            http_client = httpx.Client(
                base_url=settings.LOCAL_LLM_BASE_URL, timeout=settings.LOCAL_LLM_TIMEOUT
            )
        self._http = http_client
        self._prompt = (_PROMPTS_DIR / "ocr_page.txt").read_text(encoding="utf-8")

    def extract_page(self, image: bytes) -> str:
        resp = self._http.post(
            "/api/chat",
            json={
                "model": settings.LOCAL_VISION_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": self._prompt,
                        "images": [base64.b64encode(image).decode()],
                    }
                ],
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def get_ocr_client() -> OcrClient:
    """依 AI_PROVIDER 選抽字後端。mock 不接 OCR 是刻意的(demo 模式不呼叫任何模型)。"""
    if settings.AI_PROVIDER == "aws":
        return BedrockOcrClient()
    if settings.AI_PROVIDER == "local":
        return OllamaOcrClient()
    raise OcrUnavailableError("本版未接 OCR,請改用電子檔或直接貼上文字。")
