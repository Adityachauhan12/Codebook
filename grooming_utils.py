import base64
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1/models/"
    f"gemini-2.5-pro:generateContent?key={API_KEY}"
)

FACE_GROOMING_PROMPT = """
You are assessing IndiGo Airlines cabin crew grooming standards. Be concise but complete.

⚠️ CRITICAL RULE - VISIBILITY-BASED SCORING:
- Judge ONLY what is VISIBLE in the image/video
- If an entire category is NOT VISIBLE, award FULL MARKS with "(NOT VISIBLE)" label
- DEDUCT marks ONLY for visible violations
- List NOT VISIBLE items in "Issues Found" for crew awareness
- Do NOT penalize for items outside camera frame

HAIRSTYLE (2.0 max):
- Approved styles: Chignon, Braid Roll, Twisted Bun, Centre Braid, Side Braids, Centre Partition with Braided Band, Natural Curls, Soft Curls
- Approved colors: Ash Grey, Caramel, Hot Toffee, Sparkling Amber, Chocolate Cherry, Midnight Ruby/Burgundy
- Highlights: fine (≤2mm), blended. Ponytails: NOT allowed
- If VISIBLE + compliant: Score 2.0/2
- If VISIBLE + violation: Deduct [Style 0.6 + Neatness 0.6 + Color 0.4 + Finish 0.4]
- If NOT VISIBLE: Score 2.0/2 → output "Hairstyle: 2.0/2 (NOT VISIBLE)"

MAKEUP (2.0 max):
- Base: must match skin tone
- Eyes: approved palettes only (Swiss Beauty 05, Makeup Revolution Achieve, Nykaa Eyes On Me Night Out, Lakme Smokey Glam, Nykaa Tinsel Twilight, Sugar Blend 02)
- Liner: brown (fair-wheatish), black (dusky-olive)
- Mascara: black, volumizing
- Lips: burgundy, wine, cranberry
- If VISIBLE + compliant: Score 2.0/2
- If VISIBLE + violation: Deduct [Base 0.5 + Eyes 0.5 + Lips 0.5 + Overall 0.5]
- If NOT VISIBLE: Score 2.0/2 → output "Makeup: 2.0/2 (NOT VISIBLE)"

NAILS (1.0 max):
- Colors: Pearl white or French manicure
- If VISIBLE + compliant: Score 1.0/1
- If VISIBLE + violation: Deduct [Length 0.5 + Color 0.5]
- If NOT VISIBLE: Score 1.0/1 → output "Nails: 1.0/1 (NOT VISIBLE)"

ACCESSORIES (2.0 max):
- Earrings: white/rose-gold pearl or diamond studs only
- Rings: max one per hand, silver/rose-gold, ring or middle finger only
- Watch: must have seconds hand (black/silver/rose-gold/dark blue)
- Bangles: max one plain silver/rose-gold
- Prohibitions: NO religious threads, nose pins, extra piercings
- If VISIBLE + compliant: Score 2.0/2
- If VISIBLE + violation: Deduct [Rings 0.5 + Earrings 0.5 + Watch 0.5 + Prohibitions 0.5]
- If NOT VISIBLE: Score 2.0/2 → output "Accessories: 2.0/2 (NOT VISIBLE)"

UNIFORM (3.0 max):
- Tunic: clean, well-fitted
- Scarf: correct knot, properly positioned
- Badge: visible, aligned
- Stockings: compliant color
- If VISIBLE + compliant: Score 3.0/3
- If VISIBLE + violation: Deduct [Tunic 1.0 + Scarf 0.5 + Badge 0.5 + Stockings 0.5 + Overall 0.5]
- If NOT VISIBLE: Score 3.0/3 → output "Uniform: 3.0/3 (NOT VISIBLE)"

COMPLIANCE THRESHOLD:
Score >= 7.0 = COMPLIANT
Score < 7.0 = NON-COMPLIANT

OUTPUT FORMAT (exact):
Overall Assessment: [COMPLIANT or NON-COMPLIANT]
Score: X.X/10

Category Scores:
Uniform: X.X/3
Hairstyle: X.X/2
Makeup: X.X/2
Nails: X.X/1
Accessories: X.X/2

Observations:
- Uniform: [Description, include "(NOT VISIBLE)" if applicable]
- Hairstyle: [Description, include "(NOT VISIBLE)" if applicable]
- Makeup: [Description, include "(NOT VISIBLE)" if applicable]
- Nails: [Description, include "(NOT VISIBLE)" if applicable]
- Accessories: [Description, include "(NOT VISIBLE)" if applicable]

Issues Found:
- [Only visible violations, OR items marked as NOT VISIBLE]

Recommendations:
- [Actionable fixes for violations and guidance on non-visible items]
"""


def _post_gemini(parts):
    """Send request to Gemini API and return text response."""
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": parts}]}
    r = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""


def _normalize_output(report_text: str) -> str:
    """Normalize scores to X.X format."""
    # Remove Evidence tags if any
    report_text = re.sub(r'\s*\(Evidence:[^)]*\)', '', report_text, flags=re.IGNORECASE)
    
    # Fix Overall Score
    report_text = re.sub(
        r'(Score)\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10',
        lambda m: f"{m.group(1)}: {float(m.group(2)):.1f}/10",
        report_text,
        flags=re.IGNORECASE
    )
    
    # Fix category scores
    category_maxes = {"Uniform": 3.0, "Hairstyle": 2.0, "Makeup": 2.0, "Nails": 1.0, "Accessories": 2.0}
    
    for cat, maxval in category_maxes.items():
        pattern = rf'({re.escape(cat)}\s*:\s*)([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)'
        def replace_score(m):
            num = float(m.group(2))
            return f"{m.group(1)}{num:.1f}/{m.group(3)}"
        report_text = re.sub(pattern, replace_score, report_text, flags=re.IGNORECASE)
    
    return report_text


def check_grooming(image_b64: str) -> str:
    """Single image assessment - concise format."""
    prompt = f"{FACE_GROOMING_PROMPT}"
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
    ]
    raw = _post_gemini(parts)
    return _normalize_output(raw)


def check_grooming_from_video(video_path: str, name: str, iga_code: str) -> str:
    """Video assessment - concise format with crew info."""
    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        f"{FACE_GROOMING_PROMPT}\n\n"
        "This is a video. Sample frames uniformly. Use majority rule for assessment.\n"
        f"Output format:\nOverall Assessment: [COMPLIANT or NON-COMPLIANT]\nScore: X.X/10\n..."
    )
    parts = [
        {"text": prompt},
        {"inline_data": {"mime_type": "video/mp4", "data": video_b64}},
    ]
    raw = _post_gemini(parts)
    return _normalize_output(raw)


def check_grooming_from_frames(frames_b64: list, name: str, iga_code: str) -> str:
    """Multiple frames assessment."""
    results = []
    frame_scores = []
    
    for idx, image_b64 in enumerate(frames_b64, start=1):
        reply = check_grooming(image_b64)
        results.append(f"--- Frame {idx} ---\n{reply}")
        
        score_match = re.search(r'Score:\s*([0-9.]+)/10', reply)
        if score_match:
            frame_scores.append(float(score_match.group(1)))
    
    avg_score = sum(frame_scores) / len(frame_scores) if frame_scores else 0.0
    summary = f"CONSOLIDATED: {name} (IGA: {iga_code})\nAverage Score: {avg_score:.1f}/10.0\nAssessment: {'COMPLIANT' if avg_score >= 7.0 else 'NON-COMPLIANT'}\n\n"
    
    return summary + "\n".join(results)
