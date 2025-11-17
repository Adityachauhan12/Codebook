
"""
A toolkit for communicating with the Google Gemini API for grooming checks.
This module provides standalone functions to assess grooming standards by sending
image or video data to the Gemini API along with a detailed prompt.
"""

import base64
import re
import google.auth
import google.generativeai as genai

# Setup Gemini with ADC
SCOPES = ["https://www.googleapis.com/auth/generative-language"]
creds, _ = google.auth.default(scopes=SCOPES)
genai.configure(credentials=creds)

model = genai.GenerativeModel("models/gemini-2.5-pro", generation_config={"temperature": 0.0})

# Core prompt: policy + rubric - UPDATED WITH GENDER CHECK
FACE_GROOMING_PROMPT = """
You are assessing IndiGo Airlines cabin crew grooming standards. Be concise but complete.

⚠️ CRITICAL: GENDER CHECK FIRST
- Determine if the crew member is MALE or FEMALE
- If MALE: State "ASSESSMENT CANNOT BE COMPLETED: These standards are for FEMALE cabin crew only. Male crew requires different grooming standards."
- If FEMALE: Continue with full assessment below
- If UNCLEAR/AMBIGUOUS: Request clarification

If you determine the crew is MALE, STOP assessment and output:
Overall Assessment: NON-COMPLIANT
Score: 0/10
Reason: These grooming standards apply to FEMALE cabin crew only. Male crew member cannot be assessed using these standards.

Issues Found:
- Assessment criteria are gender-specific (female only)
- Male crew member does not comply with female standards

Recommendations:
- Please use appropriate male cabin crew grooming standards for assessment
- These standards are exclusively for female IndiGo cabin crew

---

⚠️ CRITICAL RULE - VISIBILITY-BASED SCORING (FOR FEMALE CREW ONLY):
- Judge what IS VISIBLE in the image/video
- IMPORTANT: If crew appears in casual attire (not uniform), evaluate that attire for any grooming standards applicable to it
- If uniform is completely absent/not visible AND cannot judge any standards: score as NOT VISIBLE (full marks with marker)
- DEDUCT marks ONLY for visible violations
- List NOT VISIBLE items in "Issues Found" for crew awareness
- Do NOT give full marks just because something is off-frame; only mark NOT VISIBLE if truly impossible to assess

HAIRSTYLE (2.0 max):
- Approved styles: Chignon, Braid Roll, Twisted Bun, Centre Braid, Side Braids, Centre Partition with Braided Band, Natural Curls, Soft Curls
- Approved colors: Ash Grey, Caramel, Hot Toffee, Sparkling Amber, Chocolate Cherry, Midnight Ruby/Burgundy
- Highlights: fine (≤2mm), blended. Ponytails: NOT allowed
- If VISIBLE + compliant: Score 2/2
- If VISIBLE + violation: Deduct marks appropriately
- If NOT VISIBLE (truly cannot assess): Score 2/2 → output "Hairstyle: 2/2 (NOT VISIBLE)"

MAKEUP (2.0 max):
- Base: must match skin tone
- Eyes: approved palettes only (Swiss Beauty 05, Makeup Revolution Achieve, Nykaa Eyes On Me Night Out, Lakme Smokey Glam, Nykaa Tinsel Twilight, Sugar Blend 02)
- Liner: brown (fair-wheatish), black (dusky-olive)
- Mascara: black, volumizing
- Lips: burgundy, wine, cranberry
- If VISIBLE + compliant: Score 2/2
- If VISIBLE + violation: Deduct marks appropriately
- If NOT VISIBLE (truly cannot assess): Score 2/2 → output "Makeup: 2/2 (NOT VISIBLE)"

NAILS (1.0 max):
- Colors: Pearl white or French manicure ONLY
- If nails are shown/visible (hands visible): ALWAYS evaluate - do NOT mark as NOT VISIBLE
- If VISIBLE + compliant: Score 1/1
- If VISIBLE + non-compliant color/length: Score 0/1 (deduct full marks)
- If truly hands completely out of frame: Score 1/1 → output "Nails: 1/1 (NOT VISIBLE)"

ACCESSORIES (2.0 max):
- Earrings: white/rose-gold pearl or diamond studs only [0.4 each if wrong]
- Rings: max one per hand, silver/rose-gold, ring or middle finger only [0.5 if violation]
- Watch: MUST have visible seconds hand (black/silver/rose-gold/dark blue) [0.5 if wrong/missing]
- Bangles: max one plain silver/rose-gold [0.4 if excess]
- Prohibitions: NO religious threads, nose pins, extra piercings [0.4 each if present]
- If all compliant and visible: Score 2/2
- If violations visible: Deduct per breakdown above (total max deduction = 2.0)
- If NOT VISIBLE (truly cannot assess any accessories): Score 2/2 → output "Accessories: 2/2 (NOT VISIBLE)"
- IMPORTANT: If ANY accessory is visible, do NOT mark entire category as NOT VISIBLE

UNIFORM (3.0 max):
- Tunic: clean, well-fitted
- Scarf: correct knot, properly positioned
- Badge: visible, aligned
- Stockings: compliant color
- If VISIBLE + compliant: Score 3/3
- If VISIBLE + violation OR in casual attire: Deduct marks appropriately (do NOT give full marks just because not in proper uniform)
- If NOT VISIBLE (truly off-frame, impossible to assess): Score 3/3 → output "Uniform: 3/3 (NOT VISIBLE)"

SCORING RULES (CRITICAL):
- All scores MUST be integers (0, 1, 2, 3), NO DECIMALS
- Example: "Hairstyle: 1/2" NOT "Hairstyle: 1.5/2"
- Overall score MUST be integer (0-10), NO DECIMALS
- Calculate: sum all category scores, cap at 10

COMPLIANCE THRESHOLD:
Score >= 7 = COMPLIANT
Score < 7 = NON-COMPLIANT

OUTPUT FORMAT (exact):
Overall Assessment: [COMPLIANT or NON-COMPLIANT]
Score: X/10

Category Scores:
Uniform: X/3
Hairstyle: X/2
Makeup: X/2
Nails: X/1
Accessories: X/2

Observations:
- Uniform: [Description, include "(NOT VISIBLE)" only if truly cannot assess]
- Hairstyle: [Description, include "(NOT VISIBLE)" only if truly cannot assess]
- Makeup: [Description, include "(NOT VISIBLE)" only if truly cannot assess]
- Nails: [Description, include "(NOT VISIBLE)" only if truly cannot assess]
- Accessories: [Description, include "(NOT VISIBLE)" only if truly cannot assess]

Issues Found:
- [Only visible violations, OR items marked as NOT VISIBLE]

Recommendations:
- [Actionable fixes for violations and guidance on non-visible items]
"""


def _normalize_output(report_text: str) -> str:
    """Normalize scores to integer format (X/10, not X.X/10)."""
    # Remove Evidence tags if any
    report_text = re.sub(r'\s*\(Evidence:[^)]*\)', '', report_text, flags=re.IGNORECASE)
    
    # Fix Overall Score - remove decimals, make it integer
    report_text = re.sub(
        r'(Score)\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10',
        lambda m: f"{m.group(1)}: {int(float(m.group(2)))}/10",
        report_text,
        flags=re.IGNORECASE
    )
    
    # Fix category scores - convert to integers, remove decimals
    category_maxes = {"Uniform": 3, "Hairstyle": 2, "Makeup": 2, "Nails": 1, "Accessories": 2}
    
    for cat, maxval in category_maxes.items():
        pattern = rf'({re.escape(cat)}\s*:\s*)([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+)'
        def replace_score(m):
            num = int(float(m.group(2)))
            return f"{m.group(1)}{num}/{m.group(3)}"
        report_text = re.sub(pattern, replace_score, report_text, flags=re.IGNORECASE)
    
    return report_text


def check_grooming(image_b64: str) -> str:
    """Single image assessment - concise format."""
    try:
        image_data = base64.b64decode(image_b64)
        response = model.generate_content(
            [
                FACE_GROOMING_PROMPT,
                {
                    "mime_type": "image/jpeg",
                    "data": image_data
                }
            ]
        )
        return _normalize_output(response.text)
    except Exception as e:
        return f"Error processing image: {str(e)}"


def check_grooming_from_video(video_path: str, name: str, iga_code: str) -> str:
    """Video assessment - concise format with crew info."""
    try:
        with open(video_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode("utf-8")

        video_data = base64.b64decode(video_b64)
        
        prompt = (
            f"{FACE_GROOMING_PROMPT}\n\n"
            "This is a video. Sample frames uniformly. Use majority rule for assessment.\n"
            f"Output format:\nOverall Assessment: [COMPLIANT or NON-COMPLIANT]\nScore: X/10\n..."
        )
        
        response = model.generate_content(
            [
                prompt,
                {
                    "mime_type": "video/mp4",
                    "data": video_data
                }
            ]
        )
        
        return _normalize_output(response.text)
    except Exception as e:
        return f"Error processing video: {str(e)}"


def check_grooming_from_frames(frames_b64: list, name: str, iga_code: str) -> str:
    """Multiple frames assessment - UPDATED for integer scores."""
    results = []
    frame_scores = []
    
    for idx, image_b64 in enumerate(frames_b64, start=1):
        reply = check_grooming(image_b64)
        results.append(f"--- Frame {idx} ---\n{reply}")
        
        score_match = re.search(r'Score:\s*([0-9]+)/10', reply)
        if score_match:
            frame_scores.append(int(score_match.group(1)))
    
    avg_score = int(sum(frame_scores) / len(frame_scores)) if frame_scores else 0
    summary = f"CONSOLIDATED: {name} (IGA: {iga_code})\nAverage Score: {avg_score}/10\nAssessment: {'COMPLIANT' if avg_score >= 7 else 'NON-COMPLIANT'}\n\n"
    
    return summary + "\n".join(results)
