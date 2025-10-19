# src/ocr_azure.py
import io
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from PIL import Image
from . import config

DOC_CLIENT = None
if config.AZURE_OCR_ENDPOINT and config.AZURE_OCR_KEY:
    DOC_CLIENT = DocumentAnalysisClient(
        endpoint=config.AZURE_OCR_ENDPOINT,
        credential=AzureKeyCredential(config.AZURE_OCR_KEY)
    )

def ocr_crop(pil_img, timeout_seconds=60):
    if not DOC_CLIENT:
        return ""  # gracefully skip OCR if not configured
    buf = io.BytesIO()
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    pil_img.save(buf, format="PNG"); buf.seek(0)
    try:
        poller = DOC_CLIENT.begin_analyze_document("prebuilt-read", document=buf)
        result = poller.result(timeout=timeout_seconds)
    except HttpResponseError:
        # quota exhausted or other service error; skip OCR but do not crash
        return ""
    lines = []
    for page in result.pages:
        for line in page.lines:
            lines.append(line.content)
    return " ".join(lines).strip()
