"""
Gemini 1536-Dimensional Embedding Service
Centralized service using google.genai for 1536-dim embeddings
"""

from google import genai
from google.genai import types
import os
from typing import List
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class GeminiEmbeddingService:
    """Singleton service for Gemini 1536-dimensional embeddings"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required for production")
        
        # Use Google GenAI client for 1536 dimensions
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-embedding-001"
        self.dimensions = 1536
        self._initialized = True
    
    def embed_query(self, text: str) -> List[float]:
        """Generate 1536-dimensional embedding for text"""
        try:
            result = self.client.models.embed_content(
                model=self.model,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=self.dimensions)
            )
            
            [embedding_obj] = result.embeddings
            return list(embedding_obj.values)
            
        except Exception as e:
            logger.error(f"Gemini embedding failed: {e}")
            raise RuntimeError(f"Embedding generation failed: {e}")
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts"""
        try:
            return [self.embed_query(text) for text in texts]
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            raise RuntimeError(f"Batch embedding generation failed: {e}")

    def generate_embedding(self, text: str) -> List[float]:
        """Synchronous embedding generation for standard service callers"""
        return self.embed_query(text)

    async def generate_embedding_async(self, text: str) -> List[float]:
        """Asynchronous embedding generation using thread pool"""
        import asyncio
        return await asyncio.to_thread(self.embed_query, text)

# Global instance for backward compatibility. Built on first use rather than at
# import, so modules in this chain can be imported without an API key present.
from backend.shared.utils.lazy import LazyProxy  # noqa: E402

embedding = LazyProxy(GeminiEmbeddingService, "embedding")