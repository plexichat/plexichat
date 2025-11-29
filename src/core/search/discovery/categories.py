"""
Category manager - Manage server discovery categories.
"""

from typing import List, Optional, Dict, Any

import utils.logger as logger

from ..models import ServerCategory
from ..exceptions import CategoryNotFoundError


class CategoryManager:
    """Manage server discovery categories."""
    
    def __init__(self, db):
        self._db = db
        self._cache: Dict[str, ServerCategory] = {}
        self._cache_loaded = False
    
    def get_all_categories(self) -> List[ServerCategory]:
        """Get all available categories."""
        self._ensure_cache()
        
        categories = list(self._cache.values())
        return sorted(categories, key=lambda c: c.id)
    
    def get_category(self, category_id: str) -> Optional[ServerCategory]:
        """Get a category by ID."""
        self._ensure_cache()
        return self._cache.get(category_id)
    
    def category_exists(self, category_id: str) -> bool:
        """Check if a category exists."""
        self._ensure_cache()
        return category_id in self._cache
    
    def validate_category(self, category_id: str) -> str:
        """Validate and return category ID, raising if invalid."""
        if not self.category_exists(category_id):
            raise CategoryNotFoundError(
                f"Category '{category_id}' does not exist",
                category=category_id
            )
        return category_id
    
    def get_category_server_count(self, category_id: str) -> int:
        """Get number of servers in a category."""
        row = self._db.fetch_one(
            """SELECT COUNT(*) as count FROM search_server_listings 
               WHERE category = ?""",
            (category_id,)
        )
        return row["count"] if row else 0
    
    def update_category_counts(self):
        """Update server counts for all categories."""
        self._ensure_cache()
        
        for category_id in self._cache:
            count = self.get_category_server_count(category_id)
            self._cache[category_id].server_count = count
    
    def _ensure_cache(self):
        """Ensure category cache is loaded."""
        if self._cache_loaded:
            return
        
        rows = self._db.fetch_all(
            "SELECT id, name, description, icon, position FROM search_categories ORDER BY position"
        )
        
        for row in rows:
            self._cache[row["id"]] = ServerCategory(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                icon=row["icon"],
                server_count=0,
            )
        
        self._cache_loaded = True
    
    def invalidate_cache(self):
        """Invalidate the category cache."""
        self._cache.clear()
        self._cache_loaded = False
