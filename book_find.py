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
    if not url:
        return url
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

ALL_BOOKS = [
    "Scrum — Jeff Sutherland & JJ Sutherland",
    "The Innovator's Dilemma by Clayton M. Christensen",
    "Human + Machine by Paul R. Daugherty and H. James Wilson",
    "How They Started Digital by David Lester",
    "Physics of the Future by Michio Kaku",
    "Contagious by Jonah Berger",
    "Never Split the Difference by Chris Voss",
    "Antifragile by Nassim Nicholas Taleb",
    "Hit Refresh by Satya Nadella",
    "Essentialism by Greg McKeown",
    "Just Listen by Mark Goulston",
    "Open Shift by Arnaud Pascoe",
    "The Singularity Is Nearer — Ray Kurzweil",
    "Mastering the Data Paradox — Nitin Seth",
    "The Stoic Mindset — Mark Tuitert",
    "Lilliput Land — Rama Bijapurkar",
    "The Coming Wave — Mustafa Suleyman with Michael Bhaskar",
    "AI, Analytics, and the New Machine Age — Hemant Taneja",
    "Vital Upgrade — Hema Shah",
    "Quantum Marketing — Raja Rajamannar",
    "Generative AI — Tom Taulli",
    "Quantum Supremacy — Michio Kaku",
    "The Art of Thinking Clearly — Rolf Dobelli",
    "Ikigai — Héctor García & Francesc Miralles",
    "Ogilvy on Advertising — David Ogilvy",
    "Basic AI — David Shrier",
    "Supremacy — Parmy Olson",
    "Our Next Reality — Win Cathcart & Louis Rosenberg",
    "Naive — Erling Kagge",
    "Blockchain Revolution — Don Tapscott & Alex Tapscott",
    "No Filter — Sarah Frier",
    "Atomic Habits — James Clear",
    "Hooked — Nir Eyal",
    "Zero to One — Peter Thiel with Blake Masters"
]

def extract_main_title(book_full: str) -> str:
    for sep in ['—', ' by ', ' - ']:
        if sep in book_full:
            return book_full.split(sep)[0].strip()
    return book_full.strip()

def extract_text_from_image(image_path: str) -> List[str]:
    print(f"📖 Extracting text from: {image_path}")
    try:
        with open(image_path, "rb") as f:
            poller = ocr_client.begin_analyze_document("prebuilt-read", document=f)
        result = poller.result()
        lines: List[str] = []
        for page in result.pages:
            for line in page.lines:
                txt = (line.content or "").strip()
                if txt and len(txt) > 1:
                    lines.append(txt)
        print(f"✅ Extracted {len(lines)} text lines")
        if lines:
            print(f"   Sample: {lines[:5]}")
        return lines
    except Exception as e:
        print(f"❌ OCR Error: {str(e)}")
        return []

def get_books_present_via_gpt(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    if not extracted_lines:
        return []
    try:
        simple_titles = [extract_main_title(kb) for kb in known_books]
        prompt = f"""OCR text:
{extracted_lines}

Known books:
{simple_titles}

Return ONLY a JSON array of matching titles."""
        resp = gpt_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
            temperature=0.1,
            max_tokens=400,
            messages=[
                {"role": "system", "content": "Return only a JSON array of titles."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.choices[0].message.content.strip()
        if "```
            parts = content.split("```")
            if len(parts) >= 3:
                content = parts[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
        arr = json.loads(content)
        if not isinstance(arr, list):
            return []
        out = []
        seen = set()
        for sm in arr:
            if not isinstance(sm, str):
                continue
            for i, st in enumerate(simple_titles):
                if sm.lower() == st.lower() and known_books[i] not in seen:
                    seen.add(known_books[i])
                    out.append(known_books[i])
                    break
        return out
    except Exception as e:
        print(f"❌ GPT Error: {e}")
        return []

def fuzzy_match_books(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    if not extracted_lines:
        return []
    full_text = " ".join(extracted_lines).lower()
    hits: List[str] = []
    for kb in known_books:
        mt = extract_main_title(kb).lower()
        if mt in full_text:
            hits.append(kb)
            continue
        words = [w for w in mt.split() if len(w) > 3]
        if len(words) >= 2:
            matches = sum(1 for w in words if w in full_text)
            if matches >= len(words) * 0.5:
                hits.append(kb)
                continue
        if len(words) >= 2 and words[0] in full_text:
            if any(w in full_text for w in words[1:]):
                hits.append(kb)
                continue
        for line in extracted_lines:
            if difflib.SequenceMatcher(None, mt, line.lower()).ratio() > 0.6:
                hits.append(kb)
                break
    # de-dup
    out, seen = [], set()
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out
