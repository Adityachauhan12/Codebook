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
    "Scrum",
    "The Innovator's Dilemma",
    "Human + Machine",
    "How They Started Digital",
    "Physics of the Future",
    "Contagious",
    "Never Split the Difference",
    "Antifragile",
    "Hit Refresh",
    "Essentialism",
    "Just Listen",
    "Open Shift",
    "The Singularity Is Nearer",
    "Mastering the Data Paradox",
    "The Stoic Mindset",
    "Lilliput Land",
    "The Coming Wave",
    "AI, Analytics, and the New Machine Age",
    "Vital Upgrade",
    "Quantum Marketing",
    "Generative AI",
    "Quantum Supremacy",
    "The Art of Thinking Clearly",
    "Ikigai",
    "Ogilvy on Advertising",
    "Basic AI",
    "Supremacy",
    "Our Next Reality",
    "Naive",
    "Blockchain Revolution",
    "No Filter",
    "Atomic Habits",
    "Hooked",
    "Zero to One",
]


def extract_text_from_image(image_path: str) -> List[str]:
    """Extract text lines from image using Azure Document Intelligence OCR"""
    print(f"📖 Extracting text from: {image_path}")

    try:
        with open(image_path, "rb") as f:
            poller = ocr_client.begin_analyze_document("prebuilt-read", document=f)
        result = poller.result()

        lines: List[str] = []
        for page in result.pages:
            for line in page.lines:
                txt = (line.content or "").strip()
                if txt:
                    lines.append(txt)

        print(f"✅ Extracted {len(lines)} text lines")
        if lines:
            print(f"   Sample: {lines[:3]}")

        return lines
    except Exception as e:
        print(f"❌ OCR Error: {str(e)}")
        return []


def _extract_json_array_from_text(text: str) -> List:
    """
    Helper to try to reliably extract a JSON array from the model text.
    Returns the parsed array or raises ValueError.
    """
    s = text.strip()

    # If fenced code block, try to pull inner content first
    if s.startswith("```") and "```" in s[3:]:
        parts = s.split("```")
        # pick the non-empty middle parts and try them
        for part in parts[1:]:
            candidate = part.strip()
            if candidate:
                # remove an optional "json" label at the start
                if candidate.lower().startswith("json"):
                    candidate = candidate[len("json") :].strip()
                # try to parse candidate directly
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    # fallthrough and try other heuristics below
                    s = candidate
                    break

    # Try full-text parse
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Fallback: extract text between first '[' and last ']'
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = s[start : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    raise ValueError("No JSON array found in text")


def get_books_present_via_gpt(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """Use GPT to identify which known books are present in OCR text"""
    if not extracted_lines:
        return []

    print(f"🤖 Using GPT to identify books from {len(extracted_lines)} lines")

    try:
        payload = {
            "ocr_lines": extracted_lines,
            "known_book_titles": known_books,
            "instruction": "Identify which book titles from 'known_book_titles' are clearly visible in 'ocr_lines'. Look for exact or close matches. Return only a JSON array of matching titles.",
        }

        response = gpt_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
            temperature=0.0,
            max_tokens=300,
            messages=[
                {
                    "role": "system",
                    "content": "You are a book title identifier. Respond only with a JSON array of book titles that match between the OCR text and known titles. Be flexible with partial matches but confident they represent the same book.",
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
        )

        # Extract the content text safely
        content = ""
        try:
            content = response.choices[0].message.content.strip()
        except Exception:
            # Safeguard if the response shape is slightly different
            content = str(response)

        print(f"   GPT response: {content}")

        # Try to extract a JSON array from the content
        try:
            arr = _extract_json_array_from_text(content)
        except ValueError:
            # If we couldn't extract a JSON array, return empty and let caller fallback
            print("❗ Could not parse JSON array from GPT response.")
            return []

        # Normalize and map to known_books. If GPT returns slightly different titles,
        # try to map them to the closest known_book using difflib.
        seen = set()
        out: List[str] = []
        for t in arr:
            if not isinstance(t, str):
                continue
            t_stripped = t.strip()
            if t_stripped in known_books and t_stripped not in seen:
                seen.add(t_stripped)
                out.append(t_stripped)
                continue

            # Try to find a close match among known_books
            close = difflib.get_close_matches(t_stripped, known_books, n=1, cutoff=0.75)
            if close and close[0] not in seen:
                seen.add(close[0])
                out.append(close[0])

        print(f"✅ GPT identified {len(out)} books: {out}")
        return out

    except Exception as e:
        print(f"❌ GPT Error: {str(e)}")
        return []


def fuzzy_match_books(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """Fallback: case-insensitive substring + difflib similarity matching"""
    if not extracted_lines:
        return []

    print(f"🔍 Using fuzzy matching as fallback")

    # Combine all OCR text and also keep individual lines
    full_text = " ".join(extracted_lines).lower()
    lines_lower = [line.lower() for line in extracted_lines]

    hits: List[str] = []

    for kb in known_books:
        kbl = kb.lower()

        # Direct substring match in full text
        if kbl in full_text:
            hits.append(kb)
            continue

        # Match if any line contains the full book title
        if any(kbl in line for line in lines_lower):
            hits.append(kb)
            continue

        # Word-by-word matching (e.g., "Quantum" and "Marketing" both present)
        words = [w for w in kbl.split() if len(w) > 2]  # Skip short words like "by", "the"
        if words and all(word in full_text for word in words):
            hits.append(kb)
            continue

        # Partial title matching (first significant word)
        significant_words = [w for w in kbl.split() if len(w) > 3]
        if significant_words:
            first_word = significant_words[0]
            if first_word in full_text:
                other_words = significant_words[1:]
                if not other_words or sum(1 for w in other_words if w in full_text) >= len(other_words) / 2:
                    hits.append(kb)
                    continue

        # Fuzzy matching with difflib on each line
        for line in lines_lower:
            ratio = difflib.SequenceMatcher(None, kbl, line).ratio()
            if ratio > 0.7:
                hits.append(kb)
                break

    # Remove duplicates while preserving order
    seen = set()
    out = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)

    print(f"✅ Fuzzy match found {len(out)} books: {out}")
    return out
