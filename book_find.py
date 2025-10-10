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
import re

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

# Complete book list with authors for better matching
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
            print(f"   Sample: {lines[:5]}")  # Show first 5 lines
        else:
            print(f"   ⚠️  No text extracted - image may be too blurry or low quality")
        
        return lines
    except Exception as e:
        print(f"❌ OCR Error: {str(e)}")
        return []

def get_books_present_via_gpt(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """
    Use GPT to identify which known books are present in OCR text.
    Improved prompt based on reference code.
    """
    if not extracted_lines:
        print(f"⚠️  No OCR lines to analyze")
        return []
    
    print(f"🤖 Using GPT to identify books from {len(extracted_lines)} lines")
    
    try:
        # Enhanced prompt based on your reference code
        prompt = f"""You are a helpful assistant analyzing OCR text from a bookshelf image.

OCR detected text lines:
{extracted_lines}

Known book titles in the library:
{known_books}

Your task:
1. Match the OCR text against the known book titles
2. Be flexible with typos and partial matches (e.g., "MASTERING DATA PARADOX" matches "Mastering the Data Paradox")
3. Look for key words from titles (e.g., "Data Paradox" is enough to match "Mastering the Data Paradox")
4. Return ONLY the books from the known list that are clearly present in the OCR output
5. Do NOT repeat book names
6. Respond with ONLY a clean JSON array of matched book titles

Example: ["Book Title 1", "Book Title 2"]
"""
        
        messages = [
            {
                "role": "system", 
                "content": "You are a book title identifier. Respond only with a JSON array of book titles. Be flexible with OCR typos and partial matches."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ]
        
        response = gpt_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT_NAME,
            temperature=0.2,  # Lower temperature for more consistent results
            max_tokens=500,
            messages=messages,
        )
        
        content = response.choices[0].message.content.strip()
        print(f"   GPT raw response: {content}")
        
        # Clean up response - remove markdown code blocks
        if content.startswith("```
            # Extract content between code fences
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```
            if match:
                content = match.group(1).strip()
        
        # Parse JSON
        arr = json.loads(content)
        if not isinstance(arr, list):
            print(f"   ⚠️  GPT response is not a list")
            return []
        
        # Validate each entry is a string and exists in known_books
        seen = set()
        out: List[str] = []
        for t in arr:
            if isinstance(t, str):
                # Try exact match first
                if t in known_books and t not in seen:
                    seen.add(t)
                    out.append(t)
                # Try case-insensitive match
                else:
                    for kb in known_books:
                        if t.lower() == kb.lower() and kb not in seen:
                            seen.add(kb)
                            out.append(kb)
                            break
        
        print(f"✅ GPT identified {len(out)} books: {out}")
        return out
        
    except json.JSONDecodeError as e:
        print(f"❌ GPT JSON Parse Error: {str(e)}")
        print(f"   Content was: {content}")
        return []
    except Exception as e:
        print(f"❌ GPT Error: {str(e)}")
        return []

def fuzzy_match_books(extracted_lines: List[str], known_books: List[str]) -> List[str]:
    """
    Fallback: Aggressive fuzzy matching with key word detection.
    Enhanced to handle partial book spine text.
    """
    if not extracted_lines:
        return []
    
    print(f"🔍 Using fuzzy matching as fallback")
    
    # Combine all OCR text
    full_text = " ".join(extracted_lines).lower()
    print(f"   Full OCR text: {full_text[:200]}...")  # Show first 200 chars
    
    hits: List[str] = []
    
    for kb in known_books:
        # Extract main title (before — or "by")
        main_title = kb.split('—').split(' by ').strip()
        main_title_lower = main_title.lower()
        
        # Strategy 1: Full title match
        if main_title_lower in full_text:
            hits.append(kb)
            print(f"   ✓ Full match: {kb}")
            continue
        
        # Strategy 2: Key word matching (all significant words present)
        words = [w for w in main_title_lower.split() if len(w) > 3]  # Words longer than 3 chars
        if len(words) >= 2:
            matches = sum(1 for w in words if w in full_text)
            # Require at least 60% of key words to match
            if matches >= len(words) * 0.6:
                hits.append(kb)
                print(f"   ✓ Key words match ({matches}/{len(words)}): {kb}")
                continue
        
        # Strategy 3: First significant word + one other word
        if len(words) >= 2:
            first_word = words
            if first_word in full_text:
                for other_word in words[1:]:
                    if other_word in full_text:
                        hits.append(kb)
                        print(f"   ✓ Partial match ({first_word} + {other_word}): {kb}")
                        break
                if kb in hits:
                    continue
        
        # Strategy 4: Fuzzy string matching on individual lines
        for line in extracted_lines:
            line_lower = line.lower()
            # Use difflib for similarity
            ratio = difflib.SequenceMatcher(None, main_title_lower, line_lower).ratio()
            if ratio > 0.6:  # 60% similarity
                hits.append(kb)
                print(f"   ✓ Fuzzy match (ratio={ratio:.2f}): {kb} ≈ {line}")
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
