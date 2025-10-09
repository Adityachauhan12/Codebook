# app/book_find.py
import os
import json
import urllib.parse
from typing import List
from dotenv import load_dotenv
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI
import difflib

load_dotenv()

AZURE_OCR_ENDPOINT = os.getenv("AZURE_OCR_ENDPOINT")
AZURE_OCR_KEY = os.getenv("AZURE_OCR_KEY")
AZURE_OPENAI_ENDPOINT_RAW = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4o")

def _normalize_openai_endpoint(url: str) -> str:
    if not url: return url
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}/"

if not AZURE_OCR_ENDPOINT or not AZURE_OCR_KEY:
    raise RuntimeError("Missing AZURE_OCR_ENDPOINT or AZURE_OCR_KEY")

AZURE_OPENAI_ENDPOINT = _normalize_openai_endpoint(AZURE_OPENAI_ENDPOINT_RAW)
if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
    raise RuntimeError("Missing AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_API_KEY")

ocr_client = DocumentAnalysisClient(
    endpoint=AZURE_OCR_ENDPOINT,
    credential=AzureKeyCredential(AZURE_OCR_KEY),
)

gpt_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

ALL_BOOKS = ["Atomic Habits", "Zero to One", "Ikigai", "Hooked", "Scrum", "Quantum Marketing"]

def extract_text_from_image(image_path: str) -> List[str]:
    with open(image_path, "rb") as f:
        poller = ocr_client.begin_analyze_document("prebuilt-read", document=f)
    result = poller.result()
    lines: List[str] = []
    for page in result.pages:
        for line in page.lines:
            txt = (line.content or "").strip()
            if txt:
                lines.append(txt)
    return lines

def get_books_present_via_gpt(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    payload = {
        "ocr_lines": extracted_lines,
        "known_book_titles": known_books,
        "instruction": "Return only titles from known_book_titles that are clearly present in ocr_lines, as a JSON array of strings."
    }
    response = gpt_client.chat.completions.create(
        model=AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
        temperature=0.0,
        max_tokens=200,
        messages=[
            {"role": "system", "content": "You are a strict validator. Respond with a JSON array only."},
            {"role": "user", "content": json.dumps(payload)}
        ],
    )
    content = response.choices[0].message.content.strip()
    try:
        arr = json.loads(content)
        if not isinstance(arr, list):
            return []
    except Exception:
        return []
    seen = set(); out: List[str] = []
    for t in arr:
        if isinstance(t, str) and t in known_books and t not in seen:
            seen.add(t); out.append(t)
    return out

def fuzzy_match_books(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """
    Local fallback: case-insensitive substring + difflib similarity.
    """
    text = " ".join(l.lower() for l in extracted_lines)
    hits: List[str] = []
    for kb in known_books:
        kbl = kb.lower()
        if kbl in text:
            hits.append(kb)
            continue
        candidates = difflib.get_close_matches(kbl, [w.lower() for w in extracted_lines], n=1, cutoff=0.75)
        if candidates:
            hits.append(kb)
    seen = set(); out = []
    for h in hits:
        if h not in seen:
            seen.add(h); out.append(h)
    return out
