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
... 

**HAIRSTYLE ASSESSMENT:**
- Approved styles: Chignon, Braid Roll, Twisted Bun, Twisted/Braid Roll Bun with Bow, Centre Braid, Side Braids, Centre Partition with Braided Band, Natural Curls, Soft Curls
- Hair should be well-maintained, neat, and professional
- Fringe and bangs must not fall beyond the eyebrows
- Approved hair colors: Ash Grey, Caramel, Hot Toffee, Sparkling Amber, Chocolate Cherry, Midnight Ruby/Burgundy
- Highlights should be fine (not exceed 2mm width), seamlessly blended
- Ponytails are NOT allowed
 
**MAKEUP ASSESSMENT:**
- Eye shadow: Should use approved products and colors(swiss beauty 05,makeup revolution-achieve,nykaa eyes on me-night out,lakme-smoki glam,nykaa-tinsel twilight,sugar-blend the rules-02 warrior)
-eye pencil: brown(for fair to wheatish complexion),black(for dusky to olive complexion)
-eye lashes:artificial lashes permissible(recommended MAC or PAC)
-mascara:any volumizing mascara(black)
-lenses:fresh looks hazel,fresh looks grey
- Foundation: Mandatory, should match skin tone
- Concealer: Applied properly on dark circles, blemishes
- Lip color: Should be in approved shades (burgundy, wine, cranberry tones)
- Overall makeup should look professional and well-blended
- Skin should appear clear with no visible tattoos
 
**NAIL ASSESSMENT:**
- Nails must be visible for assessment IF VISIBLE mention VISIBLE IF NOT mention NOT VISIBLE.
- Nails should not be too long
- Must be neatly filed
- Approved colors: Pearl white, French manicure style
- Shape: Squoval, rounded, or square for extensions
- Must be well-maintained and polished
 
**ACCESSORIES ASSESSMENT:**
- Rings: Maximum one per hand, silver/rose gold, on ring or middle finger only
- Earrings: White/rose gold pearl studs or small diamond studs only
- Bangles: Maximum one plain silver/rose gold bangle
- Watch: Must have seconds hand, appropriate colors (black, silver, rose gold, dark blue)
- No visible religious threads, nose pins, extra piercings, or inappropriate jewelry
 
**UNIFORM ASSESSMENT:**
- Tunic: Clean, well-fitted, stain-free, properly hemmed
- Scarf: Proper knot (Flower or Square knot), positioned correctly
- Name badge: Visible and properly positioned
- Stockings: Dark blue, no ladders or wrinkles
- Overall presentation: Professional, neat, well-groomed
 
**PROHIBITED ITEMS:**
- Visible tattoos, nose pins, sindoor, mangal sutra
- Second ear piercings, religious threads
- Inappropriate nail colors or excessive length
- Unprofessional hairstyles or hair colors
- Excessive or inappropriate makeup
- Wrong accessories or jewelry
 
**ASSESSMENT INSTRUCTIONS:**
1. Examine each category systematically
2. Provide a clear "COMPLIANT" or "NON-COMPLIANT" assessment
3. List specific issues found in each category
4. Give an overall grooming score (1-10)
5. Provide specific recommendations for improvement if needed
 
**RESPONSE FORMAT:**
Overall Assessment: [COMPLIANT/NON-COMPLIANT]
 
 
Detailed Assessment:
- Hairstyle: [Assessment and specific observations]
- Makeup: [Assessment and specific observations]
- Nails: [Assessment and specific observations]
- Accessories: [Assessment and specific observations]
- Uniform: [Assessment and specific observations]
 

**SCORING INSTRUCTIONS:**
Assign a score out of 10 based on the following weightage:
- Uniform: 3 points
- Nails: 1 point
- Hairstyle: 2 points
- Makeup: 2 points
- Accessories: 2 points

Scoring Rules:
- Full points: If the category is clearly visible and fully compliant
- Partial points: If minor issues are present
- Zero points: If the category is non-compliant or not visible

**RESPONSE FORMAT:**
Overall Assessment: [COMPLIANT/NON-COMPLIANT]

Detailed Assessment:
- Hairstyle: [Assessment and specific observations]
- Makeup: [Assessment and specific observations]
- Nails: [Assessment and specific observations]
- Accessories: [Assessment and specific observations]
- Uniform: [Assessment and specific observations]

Issues Found: [List specific violations]
Recommendations: [Specific improvement suggestions]
Overall Score: [Score out of 10]
"""
 
 
def check_grooming(image_b64: str) -> str:
    """Sends a single base64 encoded image for a grooming assessment.
 
    Args:
        image_b64: A base64 encoded string of the image to be assessed.
 
    Returns:
        A string containing the formatted grooming report from the Gemini API.
    """
    data = {
        "contents": [{
            "parts": [
                {"text": FACE_GROOMING_PROMPT},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": image_b64,
                    }
                },
            ],
        }]
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(GEMINI_ENDPOINT, headers=headers, json=data)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]
 
 
def check_grooming_from_video(
    video_path: str, name: str, iga_code: str
) -> str:
    """Encodes a video file and sends it for a grooming assessment.
 
    Args:
        video_path: The file path of the video to be assessed.
        name: The name of the crew member.
        iga_code: The IGA code of the crew member.
 
    Returns:
        A formatted string containing the grooming report and crew details.
    """
    with open(video_path, "rb") as f:
        video_b64 = base64.b64encode(f.read()).decode("utf-8")
 
    data = {
        "contents": [{
            "parts": [
                {"text": FACE_GROOMING_PROMPT},
                {
                    "inline_data": {
                        "mime_type": "video/mp4",
                        "data": video_b64,
                    }
                },
            ],
        }]
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(GEMINI_ENDPOINT, headers=headers, json=data)
    response.raise_for_status()
    reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    return f"Grooming check result for {name} ({iga_code}):\n\n{reply}"
 
 
def check_grooming_from_frames(
    frames_b64: list[str], name: str, iga_code: str
) -> str:
    """Sends a list of base64 encoded frames for grooming assessment.
 
    Args:
        frames_b64: A list of base64 encoded strings of the image frames.
        name: The name of the crew member.
        iga_code: The IGA code of the crew member.
 
    Returns:
        A single formatted string containing all grooming reports and details.
    """
    results = []
    for image_b64 in frames_b64:
        reply = check_grooming(image_b64)
        results.append(reply)
    return (
        f"Grooming check results for {name} ({iga_code}):\n\n" +
        "\n\n".join(results)
    )