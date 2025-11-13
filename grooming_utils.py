"""
Grooming Assessment Module with Strict Evidence-First Rubric
- No hallucinations; only assess what is clearly visible
- NOT VISIBLE items do not deduct points
- Decimal scoring with no rounding (preserves precision)
- Sub-category breakdown for fine-grained scoring
- Compliance threshold: score >= 7.0
- NO (Evidence: Frame X.Xs) tags in output
"""

import base64
import google.auth
import google.generativeai as genai
import re
from typing import Optional, Dict, Any, List

# Setup Gemini with ADC
SCOPES = ["https://www.googleapis.com/auth/generative-language"]
creds, _ = google.auth.default(scopes=SCOPES)
genai.configure(credentials=creds)

model = genai.GenerativeModel("models/gemini-2.5-pro", generation_config={"temperature": 0.0})

# ===================================================================
# FACE_GROOMING_PROMPT: Strict, evidence-first rubric
# ===================================================================
FACE_GROOMING_PROMPT = """
SECTION: Role & Objective
- You are an AI assistant specialized in assessing grooming standards for IndiGo Airlines cabin crew using a strict, evidence‑first analytic rubric.
- Judge only what is clearly visible from provided images or uniformly sampled video frames; never speculate or infer absent details.

SECTION: Upper-Body-Only View Policy
- If only upper-body is visible, nails, rings, watch, and stockings may not be visible.
- Mark these as "NOT VISIBLE" and do not deduct points for unseen sub-criteria.
- Scoring must be strictly based on visible evidence only.

SECTION: Deterministic Evidence Protocol
- Images: assess the single image directly using the rubric and the evidence‑first rule.
- Videos: sample frames uniformly at 1 fps with a 0.5 s start offset, up to 12 frames maximum, with no randomness so repeated inputs yield identical frames and outcomes.
- For each sub‑criterion, take a majority across the sampled frames; if tied, select the stricter outcome (non‑compliant for that sub‑criterion) for safety and consistency.
- Disable nondeterministic operations and fix all seeds in the execution stack to preserve reproducibility across runs.

SECTION: Hardcoded Policies (NO SPECULATION)
- Soft curls and natural curls ARE approved hairstyles per IndiGo policy.
- If hair is in an approved style (Chignon, Braid Roll, Twisted Bun, Centre Braid, Side Braids, Natural Curls, Soft Curls, etc.), MARK COMPLIANT for style.
- If a scarf is worn in Flower knot or Square knot and positioned correctly, MARK COMPLIANT for scarf.
- If a name badge is visible and positioned on the tunic, MARK COMPLIANT for name badge.
- If an ID card is visible but worn around the neck on a chain or lanyard (not on the uniform tunic), mark as "visible but not on tunic" — do NOT penalize for this; it is auxiliary to the required name badge.
- Do not hallucinate items or violations; only assess what you see.

SECTION: Order of Checks
- Evaluate categories in this fixed order: Hairstyle → Makeup → Nails → Accessories → Uniform.
- Within each category, apply the defined sub‑criteria sequentially and record deductions clearly.

SECTION: Policy Reference (what to assess)

Hairstyle (total weight: 2.0):
- Approved styles: Chignon, Braid Roll, Twisted Bun, Twisted/Braid Roll Bun with Bow, Centre Braid, Side Braids, Centre Partition with Braided Band, Natural Curls, Soft Curls
- Ponytails are NOT allowed
- Hair must be neat and professional; fringe or bangs must NOT fall beyond the eyebrows
- Approved hair colors: Ash Grey, Caramel, Hot Toffee, Sparkling Amber, Chocolate Cherry, Midnight Ruby/Burgundy
- Highlights must be fine (≤ 2 mm) and seamlessly blended

Makeup (total weight: 2.0):
- Eye shadow palettes: Swiss Beauty 05, Makeup Revolution Achieve, Nykaa Eyes On Me Night Out, Lakme Smokey Glam, Nykaa Tinsel Twilight, Sugar Blend The Rules 02 Warrior
- Eye pencil: brown for fair→wheatish, black for dusky→olive
- Artificial lashes permissible (MAC or PAC recommended)
- Mascara: black and volumizing
- Lenses: FreshLook Hazel/Grey
- Foundation mandatory and matched; concealer on dark circles/blemishes
- Lip color: burgundy/wine/cranberry
- Overall makeup must be professional, well‑blended; skin clear; no visible tattoos

Nails (total weight: 1.0):
- Not too long; neatly filed
- Approved colors: Pearl white or French manicure
- Extensions: squoval/rounded/square
- Must be well‑maintained and polished
- If nails not visible: mark "NOT VISIBLE"; do not deduct

Accessories (total weight: 2.0):
- Rings: max one per hand in silver/rose gold on ring or middle finger only
- Earrings: white/rose gold pearl studs or small diamond studs only
- Bangles: max one plain silver/rose‑gold bangle
- Watch: must have seconds hand in black/silver/rose‑gold/dark blue
- No visible religious threads, nose pins, extra piercings, or inappropriate jewelry
- ID card on lanyard: auxiliary; do not penalize if present alongside badge

Uniform (total weight: 3.0):
- Tunic: clean, well‑fitted, stain‑free, hemmed
- Scarf: Flower knot or Square knot, positioned correctly
- Name badge: MUST be visible and positioned on the tunic
- Stockings: dark blue, no ladders/wrinkles
- Overall appearance: professional, neat, well‑groomed

Prohibited Items:
- Visible tattoos, nose pins, sindoor, mangal sutra (on body)
- Second ear piercings, religious threads
- Inappropriate nail colors/lengths (only if visible)
- Unprofessional hairstyles/colors
- Excessive/inappropriate makeup
- Wrong accessories/jewelry (only penalize if visible and incorrect)

SECTION: Analytic Scoring Rubric (total = 10.0)

Category Weights (sum = 10.0):
- Uniform: 3.0
- Nails: 1.0
- Hairstyle: 2.0
- Makeup: 2.0
- Accessories: 2.0

Sub-Criterion Deductions (DO NOT ROUND; preserve decimals):

Hairstyle (2.0 total):
  - Style compliance (0.6): deduct 0.6 for ponytail/unapproved style; 0.3 if borderline; 0 if approved style visible
  - Neatness/fringe (0.6): deduct 0.6 if fringe below brows or very messy; 0.3 for minor strays; 0 if neat
  - Color/highlights (0.4): deduct 0.4 for unapproved color/thick highlights; 0.2 if slightly over spec; 0 if approved
  - Finish/integrity (0.4): deduct 0.4 if undone; 0.2 for minor flyaways; 0 if polished
  [If hairstyle NOT VISIBLE: mark "NOT VISIBLE"; score 2.0 for this category]

Makeup (2.0 total):
  - Base (0.6): deduct 0.6 for mismatch/absent; 0.3 for slight mismatch; 0 if matched
  - Eyes (0.6): deduct 0.6 for unapproved palette/wrong liner; 0.3 for blending issues; 0 if approved
  - Lips (0.4): deduct 0.4 for off‑tone; 0.2 for borderline shade; 0 if correct tone
  - Overall (0.4): deduct 0.4 for overdone/visible tattoo; 0.2 for patchiness; 0 if professional
  [If makeup NOT VISIBLE: mark "NOT VISIBLE"; score 2.0 for this category]

Nails (1.0 total):
  - Length/shape (0.5): deduct 0.5 for long/untidy/wrong shape; 0.25 for roughness; 0 if neat
  - Color/finish (0.5): deduct 0.5 for wrong color/chipped; 0.25 for slight wear; 0 if compliant
  [If nails NOT VISIBLE: mark "NOT VISIBLE"; score 1.0 for this category]

Accessories (2.0 total):
  - Rings (0.5): deduct 0.5 for excess/wrong finger/metal; 0.25 if ambiguous; 0 if compliant or NOT VISIBLE
  - Earrings (0.5): deduct 0.5 for hoops/danglers/colored; 0.25 for oversized studs; 0 if compliant or NOT VISIBLE
  - Watch (0.5): deduct 0.5 for missing seconds hand/off‑color; 0.25 if unclear; 0 if compliant or NOT VISIBLE
  - Prohibitions (0.5): deduct 0.5 if prohibited item clearly visible; 0.25 if suggestive; 0 if none visible
  [If any sub-item NOT VISIBLE: do not deduct for that sub-item]

Uniform (3.0 total):
  - Tunic (1.0): deduct 1.0 for stains/poor fit; 0.5 for wrinkles; 0 if clean and well-fitted
  - Scarf (0.5): deduct 0.5 for wrong knot/missing; 0.25 if slightly off-position; 0 if correct knot and positioned
  - Name badge (0.5): deduct 0.5 if missing from tunic; 0.25 if misaligned; 0 if visible and correctly positioned
  - Stockings (0.5): deduct 0.5 for laddered/wrong color; 0.25 for wrinkles; 0 if NOT VISIBLE or compliant
  - Overall (0.5): deduct 0.5 if unkempt; 0.25 for lint/creases; 0 if polished
  [If full uniform NOT VISIBLE (e.g., only upper body): mark component as "NOT VISIBLE"; do not deduct]

SECTION: Prohibitions Handling
- If a prohibited item is clearly visible, record once under the relevant category and in Issues Found.
- Apply only that sub‑criterion's deduction; do not double‑count.
- Do NOT hallucinate prohibitions (e.g., do not assume a lanyard is a "prohibited chain" if it holds only the ID).

SECTION: Final Compliance Decision (NO ROUNDING)
- Calculate total score with ALL decimals preserved (e.g., 7.5, 8.2, 9.0).
- COMPLIANT if total score >= 7.0 AND no major prohibition violations
- Do NOT round individual scores or total to integers; preserve all decimal places

SECTION: Output Contract Reminder
- Respond strictly using the OUTPUT_FORMAT provided below
- Maintain section order, labels, and 1–2 line limits for category notes
- Include (NOT VISIBLE) tags where applicable
- Use decimal scores (e.g., 8.5, 7.2, 1.5) with no rounding
- DO NOT include any (Evidence: Frame X.Xs) or (Evidence: ...) tags in output
"""

# ===================================================================
# OUTPUT_FORMAT: Decimal scoring, visibility tags, sub-category detail
# ===================================================================
OUTPUT_FORMAT = """
Important: Output must include exactly these sections in order:

GROOMING ASSESSMENT RESULT

Overall Assessment: <COMPLIANT or NON-COMPLIANT>
Overall Score: <X.X>/10.0 [e.g., 8.5, 7.2, 9.0]

Category Scores:

Hairstyle: <x.x>/2.0
<1-2 line description or NOT VISIBLE>

Makeup: <x.x>/2.0
<1-2 line description or NOT VISIBLE>
[If deductions: Breakdown: Base x.x + Eyes x.x + Lips x.x + Overall x.x = total]

Nails: <x.x>/1.0
<1-2 line description or NOT VISIBLE>

Accessories: <x.x>/2.0
<1-2 line description or NOT VISIBLE>

Uniform: <x.x>/3.0
<1-2 line description or NOT VISIBLE>

Issues Found:
- <specific visible violation only if present>
- <next issue only if present>

Recommendations:
- <actionable recommendation if needed>
- <next recommendation if needed>

SCORING DETAILS (if deductions occurred):
- Hairstyle: [style: x.x, neatness: x.x, color: x.x, finish: x.x]
- Makeup: [base: x.x, eyes: x.x, lips: x.x, overall: x.x]
- Nails: [length/shape: x.x, color/finish: x.x]
- Accessories: [rings: x.x, earrings: x.x, watch: x.x, prohibitions: x.x]
- Uniform: [tunic: x.x, scarf: x.x, badge: x.x, stockings: x.x, overall: x.x]
"""

def check_grooming(image_b64: str) -> str:
    """Sends a single base64 image and returns a sectioned report that matches OUTPUT_FORMAT."""
    prompt = f"{FACE_GROOMING_PROMPT}\n\n{OUTPUT_FORMAT}"
    response = model.generate_content([
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}}
    ])
    return response.text


def check_grooming_from_video(video_path: str, name: str, iga_code: str) -> str:
    """Encodes a video and returns a sectioned report that matches OUTPUT_FORMAT."""
    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode("utf-8")
    prompt = (
        f"{FACE_GROOMING_PROMPT}\n\n"
        "Analyze this short clip for grooming compliance as if it were clear frames of the same person.\n"
        "Sample frames uniformly at 1 fps with 0.5s offset, maximum 12 frames.\n"
        "Use majority rule across frames; on tie, apply stricter (non-compliant) outcome for that sub-criterion.\n"
        "Do NOT hallucinate missing items. If nails, watch, rings, or stockings are not visible, mark (NOT VISIBLE) and do not deduct.\n"
        "Do NOT include any (Evidence: Frame X.Xs) tags in the output.\n\n"
        f"{OUTPUT_FORMAT}"
    )
    response = model.generate_content([
        {"text": prompt},
        {"inline_data": {"mime_type": "video/mp4", "data": video_b64}}
    ])
    return response.text


def check_grooming_from_frames(frames_b64: list[str], name: str, iga_code: str) -> str:
    """Sends a list of base64 frames one by one and concatenates sectioned reports."""
    results = []
    for image_b64 in frames_b64:
        reply = check_grooming(image_b64)
        results.append(reply)
    return f"Grooming check results for {name} ({iga_code}):\n\n" + "\n\n".join(results)
