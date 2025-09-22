# grooming_utils.py
"""
A toolkit for communicating with the Google Gemini API for grooming checks.

This module provides standalone functions to assess grooming standards by sending
image or video data to the Gemini API along with a detailed prompt.
"""
import base64
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1/models/"
    f"gemini-2.5-pro:generateContent?key={API_KEY}"
)

FACE_GROOMING_PROMPT = """
You are an AI assistant specialized in assessing grooming standards for
Indigo Airlines crew members.

Follow airline cabin-crew grooming norms (neat uniform, approved hairstyles,
no prohibited accessories, nails clean/compliant, appropriate makeup for role)
when assessing. Be concise and objective.

HAIRSTYLE ASSESSMENT:
- Approved styles: Chignon, Braid Roll, Twisted Bun, Twisted/Braid Roll Bun with Bow,
  Centre Braid, Side Braids, Centre Partition with Braided Band, Natural Curls, Soft Curls
- Hair should be well-maintained, neat, and professional
- Fringe and bangs must not fall beyond the eyebrows
- Approved hair colors: Ash Grey, Caramel, Hot Toffee, Sparkling Amber, Chocolate Cherry, Midnight Ruby/Burgundy
- Highlights should be fine (≤ 2mm), seamlessly blended
- Ponytails are NOT allowed

MAKEUP ASSESSMENT:
- Eye shadow: approved palettes (Swiss Beauty 05, Makeup Revolution Achieve, Nykaa Eyes On Me Night Out,
  Lakme Smokey Glam, Nykaa Tinsel Twilight, Sugar Blend The Rules 02 Warrior)
- Eye pencil: brown (fair→wheatish), black (dusky→olive)
- Artificial lashes permissible (MAC or PAC recommended)
- Mascara: any volumizing mascara (black)
- Lenses: FreshLook Hazel/Grey
- Foundation mandatory, match skin tone; concealer on dark circles/blemishes
- Lip color: burgundy/wine/cranberry tones
- Overall makeup professional and well-blended; skin clear; no visible tattoos

NAIL ASSESSMENT:
- If nails not visible, state "NOT VISIBLE"; otherwise assess
- Not too long; neatly filed
- Approved colors: Pearl white, French manicure
- Shapes: squoval/rounded/square for extensions
- Well-maintained and polished

ACCESSORIES ASSESSMENT:
- Rings: max one per hand, silver/rose gold, on ring or middle finger only
- Earrings: white/rose gold pearl studs or small diamond studs only
- Bangles: max one plain silver/rose-gold bangle
- Watch: must have seconds hand; colors: black/silver/rose-gold/dark blue
- No visible religious threads, nose pins, extra piercings, or inappropriate jewelry

UNIFORM ASSESSMENT:
- Tunic clean, well-fitted, stain-free, hemmed
- Scarf proper knot (Flower/Square), positioned correctly
- Name badge visible and positioned
- Stockings dark blue, no ladders/wrinkles
- Overall professional, neat, well-groomed

PROHIBITED ITEMS:
- Visible tattoos, nose pins, sindoor, mangal sutra
- Second ear piercings, religious threads
- Inappropriate nail colors or excessive length
- Unprofessional hairstyles or hair colors
- Excessive/inappropriate makeup
- Wrong accessories/jewelry

ASSESSMENT INSTRUCTIONS:
1) Examine each category systematically
2) Provide "COMPLIANT" or "NON-COMPLIANT"
3) List specific issues found
4) Give overall grooming score (1–10)
5) Provide specific recommendations
""".strip()

# Added: strict output contract used by both image and video so parsing is reliable
OUTPUT_FORMAT = """
Important: Output must include exactly these sections in order:

1) Overall Assessment: <COMPLIANT or NON-COMPLIANT>

2) Detailed Assessment:
- Hairstyle: <1–2 lines>
- Makeup: <1–2 lines>
- Nails: <1–2 lines>
- Accessories: <1–2 lines>
- Uniform: <1–2 lines>

3) Issues Found:
- <bullet 1>
- <bullet 2>

4) Recommendations:
- <bullet 1>
- <bullet 2>

5) Overall Score: <X>/10
- Uniform: <x>/3
- Nails: <x>/1
- Hairstyle: <x>/2
- Makeup: <x>/2
- Accessories: <x>/2
""".strip()

def _post_gemini(parts):
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": parts}]}
    r = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""

def check_grooming(image_b64: str) -> str:
    """
    Sends a single base64 image and returns a sectioned report that matches OUTPUT_FORMAT.
    """
    prompt = f"{FACE_GROOMING_PROMPT}\n\n{OUTPUT_FORMAT}"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
    ]
    return _post_gemini(parts)

def check_grooming_from_video(video_path: str, name: str, iga_code: str) -> str:
    """
    Encodes a short video and returns a sectioned report that matches OUTPUT_FORMAT.
    """
    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        f"{FACE_GROOMING_PROMPT}\n\n"
        "Analyze this short clip for grooming compliance as if it were clear frames of the same person.\n"
        "If nails are not visible, say so; otherwise assess. Base conclusions strictly on visible evidence.\n\n"
        f"{OUTPUT_FORMAT}"
    )
    # Use a widely-accepted webm mime; if you upload MP4, set mime_type to video/mp4
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "video/webm", "data": video_b64}},
    ]
    return _post_gemini(parts)

def check_grooming_from_frames(frames_b64: list[str], name: str, iga_code: str) -> str:
    """
    Sends a list of base64 frames one by one and concatenates sectioned reports.
    """
    results = []
    for image_b64 in frames_b64:
        reply = check_grooming(image_b64)
        results.append(reply)
    return f"Grooming check results for {name} ({iga_code}):\n\n" + "\n\n".join(results)
