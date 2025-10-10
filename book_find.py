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

ALL_BOOKS = ["Atomic Habits", "Zero to One", "Ikigai", "Hooked", "Scrum", "Quantum Marketing"]

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

def get_books_present_via_gpt(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """Use GPT to identify which known books are present in OCR text"""
    if not extracted_lines:
        return []
    
    print(f"🤖 Using GPT to identify books from {len(extracted_lines)} lines")
    
    try:
        payload = {
            "ocr_lines": extracted_lines,
            "known_book_titles": known_books,
            "instruction": "Identify which book titles from 'known_book_titles' are clearly visible in 'ocr_lines'. Look for exact or close matches. Return only a JSON array of matching titles."
        }
        
        response = gpt_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
            temperature=0.0,
            max_tokens=300,
            messages=[
                {"role": "system", "content": "You are a book title identifier. Respond only with a JSON array of book titles that match between the OCR text and known titles. Be flexible with partial matches but confident they represent the same book."},
                {"role": "user", "content": json.dumps(payload)}
            ],
        )
        
        content = response.choices[0].message.content.strip()
        print(f"   GPT response: {content}")
        
        # Remove markdown code blocks if present
        if content.startswith("```
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        
        arr = json.loads(content)
        if not isinstance(arr, list):
            return []
        
        seen = set()
        out: List[str] = []
        for t in arr:
            if isinstance(t, str) and t in known_books and t not in seen:
                seen.add(t)
                out.append(t)
        
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
        if len(words) > 0 and all(word in full_text for word in words):
            hits.append(kb)
            continue
        
        # Partial title matching (first significant word)
        significant_words = [w for w in kbl.split() if len(w) > 3]
        if significant_words:
            first_word = significant_words[0]
            if first_word in full_text:
                # Check if at least 50% of other significant words also present
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


