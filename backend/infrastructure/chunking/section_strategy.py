"""Section-aware chunking strategy for legal documents."""

import re
from typing import List, Dict, Any
from .base_strategy import IChunkingStrategy as ChunkingStrategy


class SectionStrategy(ChunkingStrategy):
    """Chunks text based on legal document sections and headers."""
    
    def chunk_document(self, content: str, metadata: Dict[str, Any]) -> List:
        """Compatibility method for existing interface."""
        chunks = self.chunk_text(content, metadata)
        # Convert to expected format
        results = []
        for chunk in chunks:
            result = type('ChunkResult', (), {
                'chunk_id': f"chunk_{chunk.get('chunk_index', 0)}",
                'content': chunk['content'],
                'chunk_type': chunk.get('chunk_type', 'section'),
                'start_pos': chunk.get('start_position', 0),
                'end_pos': chunk.get('end_position', 0),
                'confidence': chunk.get('quality_score', 0.8)
            })()
            results.append(result)
        return results
    
    def get_chunk_size(self) -> int:
        """Return the target chunk size."""
        return self.max_chunk_size
    """Chunks text based on legal document sections and headers."""
    
    def __init__(self, min_chunk_size: int = 500, max_chunk_size: int = 2000):
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        
        # Legal section patterns
        self.section_patterns = [
            r'^(?:ARTICLE|Article|SECTION|Section)\s+[IVX\d]+[.\s]',  # Article/Section with Roman/Arabic numerals
            r'^\d+\.\s+[A-Z][^.]*[.:]',  # Numbered sections
            r'^[A-Z][A-Z\s]{10,}:?\s*$',  # ALL CAPS headers
            r'^\([a-z]\)\s+',  # (a), (b), (c) subsections
            r'^\([0-9]+\)\s+',  # (1), (2), (3) subsections
        ]
    
    #: Two, because one is not evidence of structure. An ALL-CAPS document
    #: title — "CONFIDENTIALITY AGREEMENT" — matches the same pattern a genuine
    #: ALL-CAPS heading does, so a single hit lets a document with no sections
    #: at all claim anchored boundaries. A real sectioned contract has many.
    MIN_SECTION_ANCHORS = 2

    def has_section_headers(self, text: str) -> bool:
        """Whether the document is genuinely divided into sections.

        Not the same question as "did chunking produce sections".
        `_identify_sections` always returns at least one section for non-empty
        text — the trailing block — so a document where no pattern matched comes
        back looking exactly like a well-structured one. Chunk identity depends
        on the difference: with real headings, boundaries are anchored to the
        document's own structure and an insertion is local; without them, text
        is packed greedily and one insertion shifts every boundary after it.

        The first non-blank line is skipped, because that is the title, and a
        title is not a section.
        """
        lines = [line.strip() for line in (text or "").split("\n")]
        body = [line for line in lines if line]
        anchors = 0
        for line in body[1:]:           # [1:] — the first line is the title
            if any(re.match(p, line) for p in self.section_patterns):
                anchors += 1
                if anchors >= self.MIN_SECTION_ANCHORS:
                    return True
        return False

    def chunk_text(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Chunk text by section boundaries."""
        sections = self._identify_sections(text)
        
        if not sections:
            # Fallback to paragraph chunking if no sections found
            return self._fallback_chunk(text, metadata)
        
        chunks = []
        for section in sections:
            if len(section['content']) > self.max_chunk_size:
                # Split large sections into sub-chunks
                sub_chunks = self._split_large_section(section, text)
                chunks.extend(sub_chunks)
            else:
                chunks.append(section)
        
        # No overlap. `_add_overlap` used to prepend 20% of chunk *i* onto chunk
        # *i+1*, which made a chunk's identity depend on its neighbour — the one
        # thing identity cannot do. It also mutated `next_chunk['content']` in
        # place and then used the grown chunk as the source for the next
        # overlap, so overlap compounded down a chain of sub-chunks. In the
        # measured simulation that is what dropped one chunk to 0.584 similarity
        # against its own unedited self while everything else scored 0.998+.
        #
        # Retrieval context comes from joining neighbours by INCLUDES order at
        # query time instead, which costs nothing and is exact.
        return chunks
    
    def _identify_sections(self, text: str) -> List[Dict[str, Any]]:
        """Identify section boundaries in legal text."""
        lines = text.split('\n')
        sections = []
        current_section = []
        current_start = 0
        section_start_line = 0
        
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Check if line matches section pattern
            is_section_header = any(re.match(pattern, line_stripped) for pattern in self.section_patterns)
            
            if is_section_header and current_section:
                # Finalize previous section
                section_content = '\n'.join(current_section)
                if section_content.strip():
                    sections.append({
                        'content': section_content.strip(),
                        'start_position': current_start,
                        'end_position': current_start + len(section_content),
                        'chunk_type': 'section',
                        'size': len(section_content),
                        'section_header': current_section[0].strip() if current_section else ''
                    })
                
                # Start new section
                current_section = [line]
                current_start = text.find(line, current_start)
                section_start_line = i
            else:
                current_section.append(line)
        
        # Add final section
        if current_section:
            section_content = '\n'.join(current_section)
            if section_content.strip():
                sections.append({
                    'content': section_content.strip(),
                    'start_position': current_start,
                    'end_position': len(text),
                    'chunk_type': 'section',
                    'size': len(section_content),
                    'section_header': current_section[0].strip() if current_section else ''
                })
        
        return sections
    
    def _split_large_section(self, section: Dict[str, Any], full_text: str) -> List[Dict[str, Any]]:
        """Split large sections into manageable chunks."""
        content = section['content']
        chunks = []
        
        # Split by paragraphs within the section
        paragraphs = re.split(r'\n\s*\n', content)
        current_chunk = ""
        current_start = section['start_position']
        
        for paragraph in paragraphs:
            if current_chunk and len(current_chunk) + len(paragraph) > self.max_chunk_size:
                chunks.append({
                    'content': current_chunk.strip(),
                    'start_position': current_start,
                    'end_position': current_start + len(current_chunk),
                    'chunk_type': 'section_part',
                    'size': len(current_chunk),
                    'parent_section': section.get('section_header', '')
                })
                current_chunk = paragraph
                current_start += len(current_chunk)
            else:
                if current_chunk:
                    current_chunk += "\n\n" + paragraph
                else:
                    current_chunk = paragraph
        
        # Add final chunk
        if current_chunk:
            chunks.append({
                'content': current_chunk.strip(),
                'start_position': current_start,
                'end_position': section['end_position'],
                'chunk_type': 'section_part',
                'size': len(current_chunk),
                'parent_section': section.get('section_header', '')
            })
        
        return chunks
    
    def _fallback_chunk(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback to simple chunking when no sections detected."""
        chunks = []
        words = text.split()
        current_chunk = []
        current_size = 0
        start_pos = 0
        
        for word in words:
            if current_size + len(word) > self.max_chunk_size and current_chunk:
                chunk_content = ' '.join(current_chunk)
                chunks.append({
                    'content': chunk_content,
                    'start_position': start_pos,
                    'end_position': start_pos + len(chunk_content),
                    'chunk_type': 'fallback',
                    'size': len(chunk_content)
                })
                current_chunk = [word]
                current_size = len(word)
                start_pos += len(chunk_content) + 1
            else:
                current_chunk.append(word)
                current_size += len(word) + 1
        
        # Add final chunk
        if current_chunk:
            chunk_content = ' '.join(current_chunk)
            chunks.append({
                'content': chunk_content,
                'start_position': start_pos,
                'end_position': len(text),
                'chunk_type': 'fallback',
                'size': len(chunk_content)
            })
        
        return chunks
