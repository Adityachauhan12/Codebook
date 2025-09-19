from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# App settings
PORT = int(os.getenv('PORT', 8000))
GEMINI_ENDPOINT = os.getenv('GEMINI_ENDPOINT', '')

# --- Google Cloud Storage settings ---
# Bucket where all images, logs, and grooming results will be stored
GCS_BUCKET_NAME = os.getenv('GCS_BUCKET_NAME', 'asia-south1-prj-ifs-dev-ifs-grooming-bucket')

# Base folder inside the bucket for organizing files
# Example: Grooming-Results/YYYYMMDD/images/<IGA>/frame_123456.jpg
GCS_BASE_FOLDER = os.getenv('GCS_BASE_FOLDER', 'Grooming-Results')

# Optional: Path to service account key for local development
# On Cloud Run/GKE, use Application Default Credentials (no need for this)
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
