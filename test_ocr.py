# test_ocr.py
from app.book_find import extract_text_from_image, get_books_present_via_gpt, ALL_BOOKS, fuzzy_match_books

print("="*60)
print("Testing OCR and Book Detection")
print("="*60)

# Test BEFORE image
print("\n=== BEFORE IMAGE ===")
before_lines = extract_text_from_image("captured_frames/before.jpg")
print(f"\nAll OCR lines: {before_lines}")

books_before_gpt = get_books_present_via_gpt(before_lines, ALL_BOOKS)
if not books_before_gpt:
    print("\nGPT found nothing, trying fuzzy match...")
    books_before = fuzzy_match_books(before_lines, ALL_BOOKS)
else:
    books_before = books_before_gpt

print(f"\nFinal BEFORE books: {books_before}")

# Test AFTER image
print("\n" + "="*60)
print("=== AFTER IMAGE ===")
after_lines = extract_text_from_image("captured_frames/after.jpg")
print(f"\nAll OCR lines: {after_lines}")

books_after_gpt = get_books_present_via_gpt(after_lines, ALL_BOOKS)
if not books_after_gpt:
    print("\nGPT found nothing, trying fuzzy match...")
    books_after = fuzzy_match_books(after_lines, ALL_BOOKS)
else:
    books_after = books_after_gpt

print(f"\nFinal AFTER books: {books_after}")

# Compare
print("\n" + "="*60)
print("=== COMPARISON ===")
taken = list(set(books_before) - set(books_after))
placed = list(set(books_after) - set(books_before))
print(f"Taken: {taken}")
print(f"Placed: {placed}")
print("="*60)
