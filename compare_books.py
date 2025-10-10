# app/compare_books.py

from typing import List, Dict

def compare_book_lists(before_books: List[str], after_books: List[str]) -> Dict[str, List[str]]:
    """
    Compare before and after book lists to determine what was taken or placed.
    
    Returns:
        {
            "removed": [...],  # Books in BEFORE but not in AFTER (taken)
            "added": [...]     # Books in AFTER but not in BEFORE (placed)
        }
    """
    before_set = set(before_books)
    after_set = set(after_books)
    
    removed = list(before_set - after_set)  # Taken from shelf
    added = list(after_set - before_set)     # Placed on shelf
    
    return {
        "removed": removed,
        "added": added
    }
