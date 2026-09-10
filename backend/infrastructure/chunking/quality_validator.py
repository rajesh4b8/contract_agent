"""Quality validation using Observer pattern."""

from typing import List, Dict, Any
from abc import ABC, abstractmethod


class QualityObserver(ABC):
    """Observer interface for quality validation events."""
    
    @abstractmethod
    async def on_validation_complete(self, chunk_id: str, quality_scores: Dict[str, float]) -> None:
        """Handle validation completion event."""
        pass
    
    @abstractmethod
    async def on_validation_failed(self, chunk_id: str, issues: List[str]) -> None:
        """Handle validation failure event."""
        pass


class QualityValidator:
    """Main quality validator with observer pattern."""
    
    def __init__(self):
        self._observers: List[QualityObserver] = []
        self.boundary_checker = BoundaryQualityChecker()
        self.context_preserver = LegalContextPreserver()
        self.embedding_validator = EmbeddingQualityValidator()
    
    def add_observer(self, observer: QualityObserver) -> None:
        """Add quality observer."""
        self._observers.append(observer)
    
    async def validate_chunks(self, chunks: List[Dict[str, Any]], 
                            context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate chunk quality comprehensively."""
        validation_results = {
            'total_chunks': len(chunks),
            'passed': 0,
            'failed': 0,
            'chunk_scores': []
        }
        
        for chunk in chunks:
            chunk_id = chunk.get('chunk_id', chunk.get('id', ''))
            
            # Run all validators
            boundary_score = self.boundary_checker.check_boundaries(chunk)
            context_score = self.context_preserver.check_legal_context(chunk, context)
            embedding_score = self.embedding_validator.validate_embedding_readiness(chunk)
            
            # Combine scores
            quality_scores = {
                'boundary_quality': boundary_score,
                'legal_context': context_score,
                'embedding_readiness': embedding_score,
                'overall': (boundary_score * 0.4 + context_score * 0.3 + embedding_score * 0.3)
            }
            
            # Determine pass/fail
            if quality_scores['overall'] >= 0.6:
                validation_results['passed'] += 1
                await self._notify_validation_complete(chunk_id, quality_scores)
            else:
                validation_results['failed'] += 1
                issues = self._identify_issues(quality_scores)
                await self._notify_validation_failed(chunk_id, issues)
            
            validation_results['chunk_scores'].append({
                'chunk_id': chunk_id,
                'scores': quality_scores
            })

        # Aggregate score for callers that report or gate on overall quality.
        # Without this key, consumers raised KeyError on the *success* path and
        # fell into their own failure handler, re-chunking the whole document.
        scores = [c['scores']['overall'] for c in validation_results['chunk_scores']]
        validation_results['overall_quality'] = sum(scores) / len(scores) if scores else 0.0

        return validation_results
    
    def _identify_issues(self, quality_scores: Dict[str, float]) -> List[str]:
        """Identify specific quality issues."""
        issues = []
        
        if quality_scores['boundary_quality'] < 0.6:
            issues.append("Poor boundary quality - chunk may break mid-sentence")
        
        if quality_scores['legal_context'] < 0.6:
            issues.append("Legal context not preserved - may lose legal meaning")
        
        if quality_scores['embedding_readiness'] < 0.6:
            issues.append("Not ready for embedding - size or content issues")
        
        return issues
    
    async def _notify_validation_complete(self, chunk_id: str, quality_scores: Dict[str, float]):
        """Notify observers of successful validation."""
        for observer in self._observers:
            try:
                await observer.on_validation_complete(chunk_id, quality_scores)
            except Exception:
                pass
    
    async def _notify_validation_failed(self, chunk_id: str, issues: List[str]):
        """Notify observers of validation failure."""
        for observer in self._observers:
            try:
                await observer.on_validation_failed(chunk_id, issues)
            except Exception:
                pass


class BoundaryQualityChecker:
    """Validates sentence and clause boundaries."""
    
    def check_boundaries(self, chunk: Dict[str, Any]) -> float:
        """Check if chunk has clean boundaries."""
        content = chunk.get('content', '')
        
        if not content:
            return 0.0
        
        # Check start boundary
        start_score = self._check_start_boundary(content)
        
        # Check end boundary
        end_score = self._check_end_boundary(content)
        
        # Check for incomplete words
        word_score = self._check_word_completeness(content)
        
        return (start_score * 0.3 + end_score * 0.5 + word_score * 0.2)
    
    def _check_start_boundary(self, content: str) -> float:
        """Check if chunk starts at natural boundary."""
        first_char = content.strip()[0] if content.strip() else ''
        
        # Good starts: uppercase, digit, quote
        if first_char.isupper() or first_char.isdigit() or first_char in '"\'(':
            return 1.0
        
        # Acceptable: lowercase after common abbreviations
        if content.strip().startswith(('e.g.', 'i.e.', 'etc.')):
            return 0.8
        
        return 0.5
    
    def _check_end_boundary(self, content: str) -> float:
        """Check if chunk ends at natural boundary."""
        last_chars = content.rstrip()[-3:] if len(content.rstrip()) >= 3 else content.rstrip()
        
        # Perfect endings
        if last_chars.endswith(('.', '!', '?', ';')):
            return 1.0
        
        # Good endings
        if last_chars.endswith((':', ',')):
            return 0.8
        
        # Acceptable if ends with closing punctuation
        if last_chars.endswith((')', ']', '"', "'")):
            return 0.7
        
        return 0.4
    
    def _check_word_completeness(self, content: str) -> float:
        """Check if words are complete at boundaries."""
        words = content.split()
        
        if not words:
            return 0.0
        
        # Check first and last words
        first_word_complete = len(words[0]) > 2 and words[0][0].isalpha()
        last_word_complete = len(words[-1]) > 2 and words[-1][-1].isalnum()
        
        return (first_word_complete * 0.5 + last_word_complete * 0.5)


class LegalContextPreserver:
    """Validates legal meaning preservation."""
    
    def check_legal_context(self, chunk: Dict[str, Any], context: Dict[str, Any]) -> float:
        """Check if chunk preserves legal context."""
        content = chunk.get('content', '')
        is_legal = context.get('is_legal_document', False)
        
        if not is_legal:
            return 1.0  # Not applicable for non-legal documents
        
        # Check for legal completeness
        completeness_score = self._check_legal_completeness(content)
        
        # Check for clause integrity
        clause_score = self._check_clause_integrity(content)
        
        # Check for context preservation
        context_score = self._check_context_preservation(chunk, context)
        
        return (completeness_score * 0.4 + clause_score * 0.4 + context_score * 0.2)
    
    def _check_legal_completeness(self, content: str) -> float:
        """Enhanced legal completeness check with context preservation."""
        import re
        
        # Check for incomplete legal statements
        incomplete_patterns = [
            r'shall\s*$',  # Ends with "shall"
            r'will\s*$',   # Ends with "will"
            r'must\s*$',   # Ends with "must"
            r'agrees?\s+to\s*$',  # Ends with "agrees to"
        ]
        
        for pattern in incomplete_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return 0.3  # Heavily penalize incomplete legal statements
        
        # Check for definitional completeness
        definition_completeness = self._check_definition_completeness(content)
        
        return min(1.0, 0.7 + definition_completeness * 0.3)
    
    def _check_definition_completeness(self, content: str) -> float:
        """Check if definitions are complete and not cut off."""
        import re
        
        # Look for definition patterns
        definition_start = re.search(r'"[^"]*"\s+means', content, re.IGNORECASE)
        if definition_start:
            # Check if definition is complete (ends with period or semicolon)
            remaining = content[definition_start.end():]
            if re.search(r'[.;]', remaining):
                return 1.0  # Complete definition
            else:
                return 0.2  # Incomplete definition
        
        return 1.0  # No definition found, assume complete
    
    def _check_clause_integrity(self, content: str) -> float:
        """Check if clauses are not broken mid-statement."""
        import re
        
        # Check for clause starters and enders
        clause_starters = ['provided', 'whereas', 'therefore', 'notwithstanding']
        has_starter = any(word in content.lower() for word in clause_starters)
        
        # If has starter, should have complete clause
        if has_starter:
            # Check if ends properly
            if content.rstrip().endswith(('.', ';', ':')):
                return 1.0
            else:
                return 0.6  # Incomplete clause
        
        return 0.9  # No clause detected, likely okay
    
    def _check_context_preservation(self, chunk: Dict[str, Any], context: Dict[str, Any]) -> float:
        """Check if chunk preserves necessary context."""
        # Check if chunk has overlap with previous chunk
        has_overlap = chunk.get('has_overlap', False)
        overlap_quality = chunk.get('overlap_quality', 0)
        
        if has_overlap:
            return overlap_quality
        
        # First chunk doesn't need overlap
        if chunk.get('chunk_index', 0) == 0:
            return 1.0
        
        return 0.7  # No overlap but not first chunk


class EmbeddingQualityValidator:
    """Validates embedding generation readiness."""
    
    def validate_embedding_readiness(self, chunk: Dict[str, Any]) -> float:
        """Validate if chunk is ready for embedding generation."""
        content = chunk.get('content', '')
        
        # Check size
        size_score = self._check_size(content)
        
        # Check content quality
        quality_score = self._check_content_quality(content)
        
        # Check if embedding already exists
        has_embedding = 'embedding' in chunk and chunk['embedding']
        embedding_score = 1.0 if has_embedding else 0.8
        
        return (size_score * 0.4 + quality_score * 0.4 + embedding_score * 0.2)
    
    def _check_size(self, content: str) -> float:
        """Check if size is optimal for embeddings."""
        char_count = len(content)
        token_estimate = char_count // 4
        
        # Optimal range: 50-512 tokens
        if 50 <= token_estimate <= 512:
            return 1.0
        elif 20 <= token_estimate <= 600:
            return 0.8
        elif token_estimate < 20:
            return 0.4  # Too small
        else:
            return 0.5  # Too large
    
    def _check_content_quality(self, content: str) -> float:
        """Check content quality for embeddings."""
        if not content or len(content.strip()) < 10:
            return 0.0
        
        # Check for meaningful content
        words = content.split()
        if len(words) < 5:
            return 0.5
        
        # Check for alphanumeric content
        has_alnum = any(c.isalnum() for c in content)
        if not has_alnum:
            return 0.3
        
        # Check for excessive special characters
        special_char_ratio = sum(1 for c in content if not c.isalnum() and not c.isspace()) / len(content)
        if special_char_ratio > 0.3:
            return 0.6
        
        return 1.0


# Observer implementations
class QualityMetricsCollector(QualityObserver):
    """Collects quality metrics for analysis."""
    
    def __init__(self):
        self.passed_chunks = 0
        self.failed_chunks = 0
        self.quality_scores = []
    
    async def on_validation_complete(self, chunk_id: str, quality_scores: Dict[str, float]):
        """Record successful validation."""
        self.passed_chunks += 1
        self.quality_scores.append(quality_scores['overall'])
    
    async def on_validation_failed(self, chunk_id: str, issues: List[str]):
        """Record validation failure."""
        self.failed_chunks += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get collected metrics."""
        avg_quality = sum(self.quality_scores) / len(self.quality_scores) if self.quality_scores else 0
        
        return {
            'passed_chunks': self.passed_chunks,
            'failed_chunks': self.failed_chunks,
            'total_chunks': self.passed_chunks + self.failed_chunks,
            'pass_rate': self.passed_chunks / max(self.passed_chunks + self.failed_chunks, 1),
            'average_quality': avg_quality
        }