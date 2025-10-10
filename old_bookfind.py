# === CONFIGURE YOUR AZURE INFO ===


from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI



# === Inputs ===
IMAGE_PATH = "C:/fastapi_test/surveillance/1.jpg"

all_books = [
    "Scrum — Jeff Sutherland & JJ Sutherland",
    "The Innovator’s Dilemma by Clayton M. Christensen",
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



# === Azure Form Recognizer Client ===
ocr_client = DocumentAnalysisClient(
    endpoint=AZURE_ENDPOINT,
    credential=AzureKeyCredential(AZURE_KEY)
)

# === Azure OpenAI Client ===
gpt_client = AzureOpenAI(azure_endpoint=api_base ,
                    api_key=api_key,
                    api_version=api_version)

# === OCR Function ===
def extract_text_from_image(image_path):
    with open(image_path, "rb") as f:
        poller = ocr_client.begin_analyze_document("prebuilt-read", document=f)
        result = poller.result()

    extracted_lines = []
    for page in result.pages:
        for line in page.lines:
            extracted_lines.append(line.content.strip())
    return extracted_lines

# === GPT Matching Function ===
def get_books_present_via_gpt(extracted_lines, known_books):
    prompt = f"""
            You are a helpful assistant. Below is the OCR-detected text from a bookshelf image and a list of known book titles.
            
            OCR detected lines:
            {extracted_lines}
            
            Known book titles:
            {known_books}
            
            Your task: Match and return only the books from the known list that are clearly present in the OCR output. Ignore typos or partial matches that don't make sense.
            and list do not repeat the names of book.
            Respond with a clean Python list of matched book titles only.
"""
    messages = [{"role": "user", "content": prompt}]
    response = gpt_client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        max_tokens=800,
        messages=messages,
    )

    # Extract and return only the list part from GPT response
    print(extracted_lines)
    return response.choices[0].message.content.strip()

# === Main Run ===
ocr_lines = extract_text_from_image(IMAGE_PATH)
gpt_response = get_books_present_via_gpt(ocr_lines, all_books)

print("\n✅ Books Present According to GPT + OCR:\n")
print(gpt_response)
