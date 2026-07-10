"""
Parent-Child Chunking Strategy for high-precision RAG retrieval.

Implements a two-level chunking approach:
  - Parent Document (800-1000 tokens): Preserves full paragraph logic and Markdown
    structure. These are ultimately fed to the LLM as context.
  - Child Chunk (150-200 tokens): Smaller granularity for precise vector embedding
    and high-recall dense retrieval.

Architecture:
    Raw Document
        │
        ▼
    Parent Chunks (800-1000 tokens, structure-aware)
        │
        ├── parent_id = uuid4
        ├── stored in ParentStore (JSON KV)
        │
        ▼
    Child Chunks (150-200 tokens, split from parent)
        │
        ├── metadata.parent_id → links back to parent
        ├── stored in Chroma (vector embedding)
        │
        ▼
    Retrieval: search child chunks → resolve parent via parent_id
        → deduplicate & rerank parents → feed to LLM

Design Rationale:
- Single-level chunking forces a tradeoff between retrieval granularity
  (small chunks = precise but context-poor) and LLM context quality
  (large chunks = context-rich but diffuse embeddings).
- Parent-Child decouples these: embed small for precision, retrieve large for quality.
- The parent_id binding preserves the exact document context needed for answers.
"""

import re
import uuid
from typing import List, Dict, Any, Optional, Tuple


class ParentChildChunker:
    """
    Two-level document chunker: generates parent chunks and their child sub-chunks.

    Parent chunks are created first by splitting on semantic boundaries (headings,
    double newlines). Then each parent is further split into smaller child chunks
    at finer granularity (sentence-level) for embedding.

    Both levels share the same parent_id so child→parent resolution is O(1).
    """

    # Approximate characters per token for Chinese-mixed text
    CHARS_PER_TOKEN = 2.0

    def __init__(
        self,
        parent_chunk_size: int = 900,      # target tokens per parent chunk
        parent_overlap: int = 50,          # overlap in tokens between parents
        child_chunk_size: int = 175,       # target tokens per child chunk
        child_overlap: int = 15,           # overlap in tokens between children
        min_chunk_size: int = 50,          # minimum tokens to keep a chunk
    ):
        self.parent_chunk_size = parent_chunk_size
        self.parent_overlap = parent_overlap
        self.child_chunk_size = child_chunk_size
        self.child_overlap = child_overlap
        self.min_chunk_size = min_chunk_size

        # Char-level equivalents
        self._parent_chars = int(parent_chunk_size * self.CHARS_PER_TOKEN)
        self._parent_overlap_chars = int(parent_overlap * self.CHARS_PER_TOKEN)
        self._child_chars = int(child_chunk_size * self.CHARS_PER_TOKEN)
        self._child_overlap_chars = int(child_overlap * self.CHARS_PER_TOKEN)
        self._min_chars = int(min_chunk_size * self.CHARS_PER_TOKEN)

        # Chinese + English sentence-ending punctuation
        self._sentence_end_pattern = re.compile(r'[。！？\.!?\n]+')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def split_text(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Split a document into parent chunks and child chunks.

        Args:
            text: Raw document text.
            metadata: Base metadata (source path, etc.) to attach to all chunks.

        Returns:
            dict with two keys:
              - "parents": List of {"parent_id": str, "content": str, "metadata": dict}
              - "children": List of {"parent_id": str, "content": str, "metadata": dict}
        """
        if not text or not text.strip():
            return {"parents": [], "children": []}

        base_meta = (metadata or {}).copy()

        # Step 1: Create parent chunks (structure-aware, larger)
        parents = self._create_parent_chunks(text, base_meta)

        # Step 2: Split each parent into smaller child chunks
        all_parents = []
        all_children = []
        for parent in parents:
            parent_id = parent["id"]
            parent_content = parent["content"]
            parent_meta = parent["metadata"]

            child_chunks = self._split_into_children(parent_content, parent_meta, parent_id)
            parent_meta["child_count"] = len(child_chunks)

            all_parents.append({
                "parent_id": parent_id,
                "content": parent_content,
                "metadata": parent_meta,
            })
            for cc in child_chunks:
                all_children.append(cc)

        return {
            "parents": all_parents,
            "children": all_children,
        }

    def split_texts(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Split multiple documents.

        Args:
            texts: List of document text strings.
            metadatas: Parallel list of metadata dicts.

        Returns:
            Same format as split_text(), but with documents concatenated.
        """
        all_parents = []
        all_children = []

        for i, text in enumerate(texts):
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            result = self.split_text(text, metadata=meta)
            all_parents.extend(result["parents"])
            all_children.extend(result["children"])

        return {"parents": all_parents, "children": all_children}

    # ------------------------------------------------------------------
    # Parent chunk creation (structure-aware, ~900 tokens)
    # ------------------------------------------------------------------

    def _create_parent_chunks(
        self,
        text: str,
        base_meta: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Create parent chunks by splitting on semantic boundaries."""
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Detect Markdown headings and split at heading boundaries first
        # This preserves document structure per the task requirement
        paragraphs = self._split_at_heading_boundaries(text)

        # Merge small paragraphs, split large ones → parents
        parents = self._merge_into_parents(paragraphs, base_meta)

        return parents

    def _split_at_heading_boundaries(self, text: str) -> List[str]:
        """
        Split text at Markdown heading boundaries while preserving heading context.

        Each heading starts a new structural unit. This ensures that section
        breaks align with document organization rather than arbitrary cut points.
        """
        # Split by headings (##, ###, etc.) — keep the heading marker with its content
        lines = text.split('\n')
        paragraphs = []
        current = []

        for line in lines:
            if re.match(r'^#{1,6}\s', line.strip()):
                if current:
                    paragraphs.append('\n'.join(current).strip())
                current = [line]
            else:
                current.append(line)

        if current:
            paragraphs.append('\n'.join(current).strip())

        # Remove any paragraphs that are just whitespace
        return [p for p in paragraphs if p.strip()]

    def _merge_into_parents(
        self,
        paragraphs: List[str],
        base_meta: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Merge paragraphs into parent chunks of target size.
        Each parent gets a unique UUID.
        """
        if not paragraphs:
            return []

        parents = []
        current_text = ""
        current_chars = 0

        for para in paragraphs:
            para_len = len(para)
            # If this paragraph alone exceeds parent size, split it internally
            if para_len > self._parent_chars * 1.5:
                # Flush current buffer first
                if current_text.strip():
                    parents.append({
                        "id": str(uuid.uuid4()),
                        "content": current_text.strip(),
                        "metadata": {**base_meta},
                    })
                    current_text = ""
                    current_chars = 0

                # Split long paragraph into parent-sized chunks at sentence boundaries
                sub_chunks = self._split_long_paragraph(para, self._parent_chars)
                for sc in sub_chunks:
                    if sc.strip():
                        parents.append({
                            "id": str(uuid.uuid4()),
                            "content": sc.strip(),
                            "metadata": {**base_meta},
                        })
                continue

            # Normal merge: add to current buffer
            if current_chars > 0:
                # Estimate if adding this para would exceed parent size
                if current_chars + 2 + para_len > self._parent_chars * 1.3:
                    # Finalize current parent
                    parents.append({
                        "id": str(uuid.uuid4()),
                        "content": current_text.strip(),
                        "metadata": {**base_meta},
                    })
                    current_text = para
                    current_chars = para_len
                else:
                    current_text += "\n\n" + para
                    current_chars += 2 + para_len
            else:
                current_text = para
                current_chars = para_len

        # Flush last
        if current_text.strip():
            parents.append({
                "id": str(uuid.uuid4()),
                "content": current_text.strip(),
                "metadata": {**base_meta},
            })

        # Apply overlap between consecutive parents
        if self.parent_overlap > 0 and len(parents) > 1:
            parents = self._add_parent_overlap(parents)

        return parents

    def _split_long_paragraph(self, paragraph: str, max_chars: int) -> List[str]:
        """Split an oversized paragraph at sentence boundaries."""
        # Find sentence boundary positions
        boundaries = [0]
        for match in self._sentence_end_pattern.finditer(paragraph):
            boundaries.append(match.end())

        if len(boundaries) <= 2:
            # No meaningful sentence boundaries; fixed-size split
            return self._fixed_size_split(paragraph, max_chars)

        chunks = []
        current = ""
        for i in range(1, len(boundaries)):
            segment = paragraph[boundaries[i-1]:boundaries[i]]
            if current and len(current) + len(segment) > max_chars * 1.2:
                chunks.append(current.strip())
                current = segment
            else:
                current += segment

        # Remaining text after last boundary
        if boundaries[-1] < len(paragraph):
            remaining = paragraph[boundaries[-1]:]
            if current and len(current) + len(remaining) > max_chars * 1.2:
                chunks.append(current.strip())
                current = remaining
            else:
                current += remaining

        if current.strip():
            chunks.append(current.strip())

        # Merge tiny final chunks
        merged = []
        for c in chunks:
            if c and len(c) < self._min_chars and merged:
                merged[-1] += "\n" + c
            elif c:
                merged.append(c)
        return merged if merged else chunks

    def _fixed_size_split(self, text: str, chunk_size: int) -> List[str]:
        """Fallback: split text into fixed-size character chunks."""
        chunks = []
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            if chunk.strip():
                chunks.append(chunk.strip())
        return chunks

    def _add_parent_overlap(
        self,
        parents: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Add context overlap from previous parent to current parent."""
        if self._parent_overlap_chars <= 0:
            return parents

        overlapped = [parents[0]]
        for i in range(1, len(parents)):
            prev = parents[i - 1]["content"]
            curr = parents[i]

            if len(prev) > self._parent_overlap_chars:
                overlap_text = prev[-self._parent_overlap_chars:]
                # Try to start at a sentence boundary
                bm = self._sentence_end_pattern.search(overlap_text)
                if bm:
                    overlap_text = overlap_text[bm.end():]
                if overlap_text:
                    curr["content"] = overlap_text + "\n...\n" + curr["content"]

            overlapped.append(curr)
        return overlapped

    # ------------------------------------------------------------------
    # Child chunk creation (small, ~175 tokens, from a single parent)
    # ------------------------------------------------------------------

    def _split_into_children(
        self,
        parent_text: str,
        base_meta: Dict[str, Any],
        parent_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Split parent text into smaller child chunks for embedding.

        Uses sentence boundaries to maintain coherence while keeping
        each child small (~175 tokens) for precise vector matching.
        """
        if not parent_text.strip():
            return []

        # Split into sentences first
        sentences = self._split_sentences(parent_text)

        # If sentences are few or very short, fallback to fixed-size
        if len(sentences) <= 2:
            child_parts = self._fixed_size_split(parent_text, self._child_chars)
            result = []
            for part in child_parts:
                result.append({
                    "parent_id": parent_id,
                    "content": part,
                    "metadata": {**base_meta, "parent_id": parent_id},
                })
            return result

        # Accumulate sentences into child-sized chunks
        children = []
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > self._child_chars * 1.3:
                if current.strip():
                    children.append({
                        "parent_id": parent_id,
                        "content": current.strip(),
                        "metadata": {**base_meta, "parent_id": parent_id},
                    })
                current = sentence
            else:
                current += sentence

        if current.strip():
            children.append({
                "parent_id": parent_id,
                "content": current.strip(),
                "metadata": {**base_meta, "parent_id": parent_id},
            })

        # Merge tiny final children
        if len(children) > 1 and len(children[-1]["content"]) < self._min_chars:
            children[-2]["content"] += "\n" + children[-1]["content"]
            children[-2]["metadata"]["child_count"] = len(children) - 1
            children.pop()

        # Apply child overlap
        if self.child_overlap > 0 and len(children) > 1:
            children = self._apply_child_overlap(children)

        return children

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences at Chinese/English sentence boundaries."""
        # Insert a marker before each sentence boundary
        marked = re.sub(r'(?<=[。！？\.!?\n])\s*', '\x00', text)
        parts = marked.split('\x00')
        return [p.strip() for p in parts if p.strip()]

    def _apply_child_overlap(
        self,
        children: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Add overlap between consecutive child chunks."""
        if self._child_overlap_chars <= 0:
            return children

        overlapped = [children[0]]
        for i in range(1, len(children)):
            prev = children[i - 1]["content"]
            curr = children[i]

            if len(prev) > self._child_overlap_chars:
                overlap_text = prev[-self._child_overlap_chars:]
                bm = self._sentence_end_pattern.search(overlap_text)
                if bm:
                    overlap_text = overlap_text[bm.end():]
                if overlap_text:
                    curr["content"] = overlap_text + "\n" + curr["content"]

            overlapped.append(curr)
        return overlapped