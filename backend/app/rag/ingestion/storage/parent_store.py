"""
ParentStore — lightweight KV storage for parent chunk content.

When using Parent-Child Chunking, child chunks (small, ~175 tokens) are
embedded and stored in Chroma for high-precision retrieval. The parent chunks
(large, ~900 tokens) contain the full context that should be fed to the LLM.

This store maps parent_id → parent_content, enabling O(1) resolution from
a retrieved child chunk to its complete parent context.

Implementation:
- In-memory dict-based for runtime (fast, no I/O during retrieval).
- JSON dump/load for persistence across service restarts.
- Stored in {CHROMA_PERSIST_DIR}/parent_store/ as per-collection JSON files.
"""

import json
import os
import threading
from typing import Dict, Optional


class ParentStore:
    """
    Thread-safe, file-backed KV store for parent chunk content.

    Each collection in Chroma has its own parent store file.
    """

    def __init__(self, persist_dir: str = "data/db/parent_store"):
        self._persist_dir = persist_dir
        self._lock = threading.Lock()
        self._stores: Dict[str, Dict[str, str]] = {}  # collection_name -> {parent_id: content}

        # Ensure persist directory exists
        os.makedirs(self._persist_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def put(self, collection: str, parent_id: str, content: str) -> None:
        """Store a parent chunk for a given collection."""
        with self._lock:
            if collection not in self._stores:
                self._stores[collection] = {}
                self._load_collection(collection)
            self._stores[collection][parent_id] = content

    def put_batch(self, collection: str, items: Dict[str, str]) -> None:
        """Store multiple parent chunks at once.

        Args:
            collection: Collection name.
            items: dict of {parent_id: content}
        """
        with self._lock:
            if collection not in self._stores:
                self._stores[collection] = {}
                self._load_collection(collection)
            self._stores[collection].update(items)

    def get(self, collection: str, parent_id: str) -> Optional[str]:
        """Retrieve parent content by parent_id.

        Returns None if not found.
        """
        with self._lock:
            if collection not in self._stores:
                self._stores[collection] = {}
                self._load_collection(collection)
            return self._stores[collection].get(parent_id)

    def get_batch(self, collection: str, parent_ids: list[str]) -> Dict[str, str]:
        """Retrieve multiple parent chunks by their IDs.

        Args:
            collection: Collection name.
            parent_ids: List of parent_id strings.

        Returns:
            dict of {parent_id: content} for IDs that exist in store.
        """
        with self._lock:
            if collection not in self._stores:
                self._stores[collection] = {}
                self._load_collection(collection)
            store = self._stores[collection]
            return {pid: store[pid] for pid in parent_ids if pid in store}

    def delete(self, collection: str, parent_id: str) -> bool:
        """Delete a single parent entry."""
        with self._lock:
            if collection not in self._stores:
                return False
            if parent_id in self._stores[collection]:
                del self._stores[collection][parent_id]
                return True
            return False

    def delete_collection(self, collection: str) -> bool:
        """Delete an entire collection's parent store."""
        with self._lock:
            if collection in self._stores:
                del self._stores[collection]
            # Also remove backing file
            filepath = self._collection_path(collection)
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return False

    def count(self, collection: str) -> int:
        """Count parent entries in a collection."""
        with self._lock:
            if collection not in self._stores:
                return 0
            return len(self._stores[collection])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def flush(self, collection: Optional[str] = None) -> None:
        """Flush parent store to disk.

        Args:
            collection: Specific collection to flush, or None for all.
        """
        with self._lock:
            if collection:
                if collection in self._stores:
                    self._save_collection(collection)
            else:
                for coll in list(self._stores.keys()):
                    self._save_collection(coll)

    def load_all(self) -> None:
        """Load all parent stores from disk."""
        if not os.path.isdir(self._persist_dir):
            return
        for fname in os.listdir(self._persist_dir):
            if fname.endswith(".json"):
                collection = fname[:-5]  # strip .json
                with self._lock:
                    self._stores[collection] = {}
                    self._load_collection(collection)

    def _collection_path(self, collection: str) -> str:
        """Get file path for a collection's parent store."""
        safe_name = collection.replace("/", "_").replace("\\", "_")
        return os.path.join(self._persist_dir, f"{safe_name}.json")

    def _save_collection(self, collection: str) -> None:
        """Write collection's parent store to JSON file."""
        filepath = self._collection_path(collection)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self._stores[collection], f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # Non-critical: in-memory store still works

    def _load_collection(self, collection: str) -> None:
        """Load collection's parent store from JSON file."""
        filepath = self._collection_path(collection)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._stores[collection].update(data)
            except Exception:
                self._stores[collection] = {}


# Singleton instance for the application
_parent_store: Optional[ParentStore] = None


def get_parent_store(persist_dir: str = "data/db/parent_store") -> ParentStore:
    """Get or create the singleton ParentStore instance."""
    global _parent_store
    if _parent_store is None:
        _parent_store = ParentStore(persist_dir=persist_dir)
        _parent_store.load_all()
    return _parent_store