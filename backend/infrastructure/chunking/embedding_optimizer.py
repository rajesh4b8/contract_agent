"""Embedding optimization using Template Method and Decorator patterns."""

import asyncio
import threading
from typing import List, Dict, Any, Tuple
from abc import ABC, abstractmethod
from dataclasses import dataclass

from backend.shared.debug import note, trace_step


@dataclass
class OptimizedChunk:
    """Optimized chunk for embedding generation."""
    content: str
    token_count: int
    embedding_ready: bool
    split_from_original: bool = False
    original_chunk_id: str = None


class EmbeddingOptimizer(ABC):
    """Template method for embedding optimization."""
    
    def __init__(self, max_tokens: int = 512):
        self.max_tokens = max_tokens
        self.chars_per_token = 4  # Approximate
        self.max_chars = max_tokens * self.chars_per_token
    
    async def optimize_chunks(self, chunks: List[Dict[str, Any]]) -> List[OptimizedChunk]:
        """Template method for chunk optimization."""
        # Step 1: Validate chunks
        validated_chunks = self._validate_chunks(chunks)
        
        # Step 2: Enforce token limits
        token_limited_chunks = self._enforce_token_limits(validated_chunks)
        
        # Step 3: Prepare for batch processing
        batch_ready_chunks = self._prepare_for_batching(token_limited_chunks)
        
        return batch_ready_chunks
    
    @abstractmethod
    def _validate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate chunks for embedding readiness."""
        pass
    
    @abstractmethod
    def _enforce_token_limits(self, chunks: List[Dict[str, Any]]) -> List[OptimizedChunk]:
        """Enforce token limits on chunks."""
        pass
    
    @abstractmethod
    def _prepare_for_batching(self, chunks: List[OptimizedChunk]) -> List[OptimizedChunk]:
        """Prepare chunks for batch processing."""
        pass


class TokenLimitEnforcer(EmbeddingOptimizer):
    """Legal-aware token limit enforcement with context preservation."""
    
    def __init__(self, max_tokens: int = 512):
        super().__init__(max_tokens)
        self.legal_splitter = LegalBoundarySplitter()
    
    def _validate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enhanced validation with legal context awareness."""
        valid_chunks = []
        for chunk in chunks:
            content = chunk.get('content', '')
            if content and len(content.strip()) > 10:
                # Add legal context flag
                chunk['is_legal_content'] = self._detect_legal_content(content)
                valid_chunks.append(chunk)
        return valid_chunks
    
    def _enforce_token_limits(self, chunks: List[Dict[str, Any]]) -> List[OptimizedChunk]:
        """Legal-aware token limit enforcement."""
        optimized_chunks = []
        
        for chunk in chunks:
            content = chunk['content']
            is_legal = chunk.get('is_legal_content', False)
            
            # Dynamic token limits based on content type
            effective_limit = 600 if is_legal else self.max_tokens
            estimated_tokens = len(content) // self.chars_per_token
            
            if estimated_tokens <= effective_limit:
                # Chunk is within limits
                optimized_chunks.append(OptimizedChunk(
                    content=content,
                    token_count=estimated_tokens,
                    embedding_ready=True,
                    original_chunk_id=chunk.get('chunk_id', '')
                ))
            else:
                # Legal-aware splitting
                split_chunks = self._split_with_legal_awareness(chunk, effective_limit)
                optimized_chunks.extend(split_chunks)
        
        return optimized_chunks
    
    def _detect_legal_content(self, content: str) -> bool:
        """Detect if content contains legal language."""
        legal_indicators = ['shall', 'whereas', 'therefore', 'party', 'agreement', 'contract']
        content_lower = content.lower()
        return sum(1 for term in legal_indicators if term in content_lower) >= 2
    
    def _split_with_legal_awareness(self, chunk: Dict[str, Any], token_limit: int) -> List[OptimizedChunk]:
        """Legal-aware splitting with context preservation."""
        content = chunk['content']
        is_legal = chunk.get('is_legal_content', False)
        
        if is_legal:
            # Try legal boundary splitting first
            legal_splits = self.legal_splitter.split_at_legal_boundaries(content, token_limit * self.chars_per_token)
            if legal_splits and self._validate_legal_splits(legal_splits):
                return self._convert_to_optimized_chunks(legal_splits, chunk.get('chunk_id', ''))
        
        # Fallback to existing sentence splitting
        return self._split_oversized_chunk(chunk, token_limit)
    
    def _split_oversized_chunk(self, chunk: Dict[str, Any], token_limit: int = None) -> List[OptimizedChunk]:
        """Enhanced sentence splitting with legal abbreviation handling."""
        content = chunk['content']
        chunk_id = chunk.get('chunk_id', '')
        max_chars = (token_limit or self.max_tokens) * self.chars_per_token
        
        # Legal-aware sentence splitting
        sentences = self._split_sentences_legal_aware(content)
        split_chunks = []
        current_content = ""
        split_index = 0
        
        for sentence in sentences:
            test_content = current_content + sentence + " "
            if len(test_content) <= max_chars:
                current_content = test_content
            else:
                # Finalize current split
                if current_content:
                    split_chunks.append(OptimizedChunk(
                        content=current_content.strip(),
                        token_count=len(current_content) // self.chars_per_token,
                        embedding_ready=True,
                        split_from_original=True,
                        original_chunk_id=f"{chunk_id}_split_{split_index}"
                    ))
                    split_index += 1
                
                # Start new split
                current_content = sentence + " "
        
        # Add final split
        if current_content:
            split_chunks.append(OptimizedChunk(
                content=current_content.strip(),
                token_count=len(current_content) // self.chars_per_token,
                embedding_ready=True,
                split_from_original=True,
                original_chunk_id=f"{chunk_id}_split_{split_index}"
            ))
        
        return split_chunks
    
    def _split_sentences_legal_aware(self, content: str) -> List[str]:
        """Split sentences while preserving legal abbreviations."""
        import re
        
        # Legal abbreviations that shouldn't end sentences
        legal_abbrevs = ['Inc.', 'Corp.', 'LLC.', 'Ltd.', 'vs.', 'etc.', 'i.e.', 'e.g.', 'Co.', 'Sec.']
        
        # Replace abbreviations temporarily
        temp_content = content
        replacements = {}
        for i, abbrev in enumerate(legal_abbrevs):
            placeholder = f"__ABBREV{i}__"
            replacements[placeholder] = abbrev
            temp_content = temp_content.replace(abbrev, placeholder)
        
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', temp_content)
        
        # Restore abbreviations
        restored_sentences = []
        for sentence in sentences:
            for placeholder, abbrev in replacements.items():
                sentence = sentence.replace(placeholder, abbrev)
            if sentence.strip():
                restored_sentences.append(sentence.strip())
        
        return restored_sentences
    
    def _validate_legal_splits(self, splits: List[str]) -> bool:
        """Validate that legal splits preserve meaning."""
        for split in splits:
            # Check for incomplete legal statements
            if split.strip().endswith(('shall', 'will', 'must', 'agrees to')):
                return False  # Incomplete obligation
        return True
    
    def _convert_to_optimized_chunks(self, splits: List[str], base_chunk_id: str) -> List[OptimizedChunk]:
        """Convert legal splits to optimized chunks."""
        return [
            OptimizedChunk(
                content=split.strip(),
                token_count=len(split) // self.chars_per_token,
                embedding_ready=True,
                split_from_original=True,
                original_chunk_id=f"{base_chunk_id}_legal_{i}"
            )
            for i, split in enumerate(splits) if split.strip()
        ]
    
    def _prepare_for_batching(self, chunks: List[OptimizedChunk]) -> List[OptimizedChunk]:
        """Mark chunks as ready for batch processing."""
        for chunk in chunks:
            chunk.embedding_ready = chunk.token_count <= self.max_tokens
        return chunks


class ChunkSizeValidator(EmbeddingOptimizer):
    """Validates chunk sizes for optimal embedding generation."""
    
    def _validate_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate chunk sizes and content quality."""
        valid_chunks = []
        
        for chunk in chunks:
            content = chunk.get('content', '')
            
            # Skip empty or too small chunks
            if len(content.strip()) < 20:
                continue
            
            # Skip chunks that are just whitespace or special characters
            if not any(c.isalnum() for c in content):
                continue
            
            valid_chunks.append(chunk)
        
        return valid_chunks
    
    def _enforce_token_limits(self, chunks: List[Dict[str, Any]]) -> List[OptimizedChunk]:
        """Create optimized chunks with size validation."""
        optimized_chunks = []
        
        for chunk in chunks:
            content = chunk['content']
            token_count = len(content) // self.chars_per_token
            
            # Determine if chunk is optimal size
            is_optimal = 50 <= token_count <= self.max_tokens
            
            optimized_chunks.append(OptimizedChunk(
                content=content,
                token_count=token_count,
                embedding_ready=is_optimal,
                original_chunk_id=chunk.get('chunk_id', '')
            ))
        
        return optimized_chunks
    
    def _prepare_for_batching(self, chunks: List[OptimizedChunk]) -> List[OptimizedChunk]:
        """Sort chunks by size for optimal batching."""
        return sorted(chunks, key=lambda x: x.token_count)


class _Counter:
    """Thread-safe tally of embedded chunks, for progress reporting only."""

    def __init__(self):
        self._lock = threading.Lock()
        self._value = 0

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value


class BatchProcessor:
    """Optimizes embedding generation with batch processing."""
    
    def __init__(self, batch_size: int = 5, max_concurrent: int = 3):
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent
    
    async def process_chunks_in_batches(self, chunks: List[OptimizedChunk], 
                                      embedding_service) -> List[Tuple[OptimizedChunk, List[float]]]:
        """Process chunks in optimized batches."""
        # Group chunks into batches
        batches = self._create_batches(chunks)
        
        # Process batches with concurrency control
        results = []
        semaphore = asyncio.Semaphore(self.max_concurrent)

        # This loop is one network call per chunk plus a rate-limit sleep between
        # each, so on a real contract it is the longest silent stretch of an
        # upload. `done` is reported as chunks land so the debug panel shows
        # progress rather than a stalled step.
        done = _Counter()

        async def process_batch(batch):
            async with semaphore:
                return await self._process_single_batch(
                    batch, embedding_service, done, len(chunks)
                )

        with trace_step("upload", "chunking.embed", chunks=len(chunks), batches=len(batches)) as step:
            # Process all batches
            batch_tasks = [process_batch(batch) for batch in batches]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            # Flatten results
            failed_batches = 0
            for batch_result in batch_results:
                if isinstance(batch_result, Exception):
                    failed_batches += 1
                    continue
                results.extend(batch_result)

            step.set(embedded=len(results), failed_batches=failed_batches)

        return results
    
    def _create_batches(self, chunks: List[OptimizedChunk]) -> List[List[OptimizedChunk]]:
        """Create optimized batches based on token count."""
        batches = []
        current_batch = []
        current_batch_tokens = 0
        
        for chunk in chunks:
            # If adding this chunk would exceed batch limits, start new batch
            if (len(current_batch) >= self.batch_size or 
                current_batch_tokens + chunk.token_count > self.batch_size * 200):
                
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_batch_tokens = 0
            
            current_batch.append(chunk)
            current_batch_tokens += chunk.token_count
        
        # Add final batch
        if current_batch:
            batches.append(current_batch)
        
        return batches
    
    async def _process_single_batch(self, batch: List[OptimizedChunk],
                                  embedding_service, done=None,
                                  total=None) -> List[Tuple[OptimizedChunk, List[float]]]:
        """Process a single batch of chunks."""
        results = []

        for chunk in batch:
            try:
                gen_async = getattr(embedding_service, "generate_embedding_async", None)
                if gen_async is not None:
                    embedding = await gen_async(chunk.content)
                else:
                    embedding = await asyncio.to_thread(
                        embedding_service.generate_embedding, chunk.content
                    )
                results.append((chunk, embedding))
                if done is not None:
                    note("upload", "chunking.embed.progress", done=done.next(), total=total)

                # Small delay to avoid rate limits
                await asyncio.sleep(0.1)

            except Exception as e:
                # Log error but continue processing
                print(f"Failed to generate embedding for chunk: {e}")
                note("upload", "chunking.embed.failed", error_type=type(e).__name__, error=str(e))
                results.append((chunk, []))
        
        return results


# Decorator for embedding optimization
class EmbeddingOptimizerDecorator:
    """Decorator that adds optimization to embedding generation."""
    
    def __init__(self, embedding_service, optimizer: EmbeddingOptimizer = None):
        self.embedding_service = embedding_service
        self.optimizer = optimizer or TokenLimitEnforcer()
        self.batch_processor = BatchProcessor()
    
    async def generate_optimized_embeddings(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate embeddings with optimization."""
        # Optimize chunks
        optimized_chunks = await self.optimizer.optimize_chunks(chunks)
        
        # Process in batches
        results = await self.batch_processor.process_chunks_in_batches(
            optimized_chunks, self.embedding_service
        )
        
        # Convert back to expected format
        embedding_results = []
        for optimized_chunk, embedding in results:
            if embedding:  # Only include successful embeddings
                embedding_results.append({
                    'chunk_id': optimized_chunk.original_chunk_id,
                    'content': optimized_chunk.content,
                    'embedding': embedding,
                    'token_count': optimized_chunk.token_count,
                    'split_from_original': optimized_chunk.split_from_original
                })
        
        return embedding_results


class LegalBoundarySplitter:
    """Splits content at legal clause boundaries for accuracy preservation."""
    
    def __init__(self):
        self.clause_patterns = [
            r'(?:PROVIDED\s+THAT|PROVIDED\s+HOWEVER)',
            r'(?:WHEREAS|THEREFORE|FURTHERMORE|NOTWITHSTANDING)',
            r'(?:IN\s+CONSIDERATION\s+OF|SUBJECT\s+TO)',
            r'(?:The\s+(?:Company|Contractor|Party)\s+(?:shall|will|agrees?\s+to))',
            r'(?:This\s+Agreement|The\s+Contract)\s+(?:shall|will)'
        ]
    
    def split_at_legal_boundaries(self, content: str, max_chars: int) -> List[str]:
        """Split content at legal clause boundaries."""
        if len(content) <= max_chars:
            return [content]
        
        # Find clause boundaries
        boundaries = self._find_clause_boundaries(content)
        if not boundaries:
            return []  # No legal boundaries found, use fallback
        
        # Split at boundaries while respecting size limits
        splits = []
        start = 0
        
        for boundary in boundaries:
            if boundary - start >= max_chars:
                # This segment is too large, need to split it
                if splits:  # Add previous segment
                    splits.append(content[start:boundary].strip())
                start = boundary
            elif len(content[start:]) <= max_chars:
                # Remaining content fits in one chunk
                splits.append(content[start:].strip())
                break
        
        # Add final segment if needed
        if start < len(content) and content[start:].strip():
            final_segment = content[start:].strip()
            if len(final_segment) <= max_chars:
                splits.append(final_segment)
        
        return [s for s in splits if s and len(s.strip()) > 20]  # Filter tiny splits
    
    def _find_clause_boundaries(self, content: str) -> List[int]:
        """Find positions where legal clauses begin."""
        import re
        boundaries = [0]  # Always start at beginning
        
        for pattern in self.clause_patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                pos = match.start()
                # Only add if it's at start of line or after punctuation
                if pos == 0 or content[pos-1] in '.;\n':
                    boundaries.append(pos)
        
        # Add sentence boundaries as potential split points
        for match in re.finditer(r'[.!?]\s+(?=[A-Z])', content):
            boundaries.append(match.end())
        
        return sorted(set(boundaries))