"""
Chunking Engine Module
Implements various text splitting strategies using LangChain based on
"The Ultimate Guide to Chunking Strategies for RAG Applications with Databricks"
"""

from typing import List, Dict, Optional
import re
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    CharacterTextSplitter,
    MarkdownTextSplitter
)

try:
    import nltk
    from nltk.tokenize import sent_tokenize
    NLTK_AVAILABLE = True
    # Try to load punkt_tab data (newer NLTK versions)
    try:
        nltk.data.find('tokenizers/punkt_tab')
    except LookupError:
        try:
            # Try downloading punkt_tab first (newer versions)
            nltk.download('punkt_tab', quiet=True)
        except:
            try:
                # Fallback to older punkt if punkt_tab fails
                nltk.download('punkt', quiet=True)
            except:
                NLTK_AVAILABLE = False
except ImportError:
    NLTK_AVAILABLE = False


def _ensure_nltk_data():
    """Ensure NLTK data is downloaded. Returns True if available."""
    if not NLTK_AVAILABLE:
        return False
    
    try:
        # Check if punkt_tab is available
        nltk.data.find('tokenizers/punkt_tab')
        return True
    except LookupError:
        try:
            # Try to download punkt_tab
            import ssl
            try:
                _create_unverified_https_context = ssl._create_unverified_context
            except AttributeError:
                pass
            else:
                ssl._create_default_https_context = _create_unverified_https_context
            
            nltk.download('punkt_tab', quiet=True)
            return True
        except:
            try:
                # Fallback to punkt
                nltk.download('punkt', quiet=True)
                return True
            except:
                return False


class ChunkingEngine:
    """Handles text chunking with various strategies from RAG best practices."""
    
    # Available chunking strategies
    STRATEGIES = {
        "Fixed-Size": "fixed",
        "Semantic (Recursive)": "semantic",
        "Recursive Character": "recursive",
        "Character": "character",
        "Markdown": "markdown",
        "Adaptive": "adaptive",
        "Sentence-Based": "sentence"
    }
    
    STRATEGY_DESCRIPTIONS = {
        "fixed": "Simple fixed-size chunking with optional overlap. Best for uniform documents.",
        "semantic": "Splits at logical boundaries (paragraphs, sections). Preserves context flow.",
        "recursive": "Hierarchical splitting with multiple separators. Good for structured text.",
        "character": "Basic character-based splitting. Fast and simple.",
        "markdown": "Markdown-aware splitting. Best for .md files and documentation.",
        "adaptive": "Adjusts chunk size based on text complexity. Advanced technique.",
        "sentence": "Splits at sentence boundaries. Maintains semantic integrity."
    }
    
    @staticmethod
    def split_text(
        text: str,
        strategy: str = "recursive",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        min_chunk_size: Optional[int] = None,
        max_chunk_size: Optional[int] = None
    ) -> List[str]:
        """
        Split text into chunks using the specified strategy.
        
        Args:
            text: The text to split.
            strategy: The chunking strategy to use.
            chunk_size: The target size of each chunk in characters.
            chunk_overlap: The number of characters to overlap between chunks.
            min_chunk_size: Minimum chunk size (for adaptive strategy).
            max_chunk_size: Maximum chunk size (for adaptive strategy).
            
        Returns:
            List of text chunks.
        """
        if not text or not text.strip():
            return []
        
        if strategy == "fixed":
            return ChunkingEngine._fixed_size_chunking(text, chunk_size, chunk_overlap)
        
        elif strategy == "semantic":
            return ChunkingEngine._semantic_chunking(text, chunk_size, chunk_overlap)
        
        elif strategy == "recursive":
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                length_function=len,
                separators=["\n\n", "\n", ". ", " ", ""]
            )
            return splitter.split_text(text)
        
        elif strategy == "character":
            splitter = CharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                length_function=len,
                separator="\n"
            )
            return splitter.split_text(text)
        
        elif strategy == "markdown":
            splitter = MarkdownTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            return splitter.split_text(text)
        
        elif strategy == "adaptive":
            min_size = min_chunk_size or int(chunk_size * 0.6)
            max_size = max_chunk_size or int(chunk_size * 1.5)
            return ChunkingEngine._adaptive_chunking(text, min_size, max_size, chunk_overlap)
        
        elif strategy == "sentence":
            return ChunkingEngine._sentence_based_chunking(text, chunk_size)
        
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
    
    @staticmethod
    def _fixed_size_chunking(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """
        Fixed-size chunking with overlap.
        Simplest approach - segments text into equally sized pieces.
        """
        splitter = CharacterTextSplitter(
            separator="\n\n",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len
        )
        return splitter.split_text(text)
    
    @staticmethod
    def _semantic_chunking(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """
        Semantic chunking that splits at logical boundaries.
        Uses recursive splitting with semantic separators to preserve context flow.
        """
        # Enhanced separators for better semantic boundaries
        splitter = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", ". ", ", ", " ", ""],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len
        )
        return splitter.split_text(text)
    
    @staticmethod
    def _adaptive_chunking(text: str, min_size: int, max_size: int, overlap: int) -> List[str]:
        """
        Adaptive chunking that adjusts chunk size based on text complexity.
        More complex sections get smaller chunks, simpler sections get larger chunks.
        """
        if not NLTK_AVAILABLE or not _ensure_nltk_data():
            # Fallback to semantic chunking if NLTK not available
            return ChunkingEngine._semantic_chunking(text, (min_size + max_size) // 2, overlap)
        
        # Split into sentences
        sentences = sent_tokenize(text)
        chunks = []
        current_chunk = []
        current_size = 0
        
        for sentence in sentences:
            sentence_len = len(sentence)
            
            if sentence_len == 0:
                continue
            
            # Calculate complexity (lexical density)
            complexity = ChunkingEngine._calculate_complexity(sentence)
            
            # Adjust target size based on complexity
            # More complex = smaller chunks
            target_size = max_size - (complexity * (max_size - min_size))
            
            # Check if adding this sentence would exceed target
            if current_size + sentence_len > target_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                
                # Add overlap
                overlap_chunk = []
                overlap_size = 0
                for prev_sentence in reversed(current_chunk):
                    if overlap_size + len(prev_sentence) <= overlap:
                        overlap_chunk.insert(0, prev_sentence)
                        overlap_size += len(prev_sentence)
                    else:
                        break
                
                current_chunk = overlap_chunk + [sentence]
                current_size = sum(len(s) for s in current_chunk)
            else:
                current_chunk.append(sentence)
                current_size += sentence_len
        
        # Add the last chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks
    
    @staticmethod
    def _sentence_based_chunking(text: str, target_size: int) -> List[str]:
        """
        Sentence-based chunking that respects sentence boundaries.
        Ensures chunks don't cut sentences in the middle.
        """
        if not NLTK_AVAILABLE or not _ensure_nltk_data():
            # Fallback to basic splitting on periods
            sentences = re.split(r'(?<=[.!?])\s+', text)
        else:
            sentences = sent_tokenize(text)
        
        chunks = []
        current_chunk = []
        current_size = 0
        
        for sentence in sentences:
            sentence_len = len(sentence)
            
            if sentence_len == 0:
                continue
            
            # If adding this sentence would exceed target and we have content
            if current_size + sentence_len > target_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [sentence]
                current_size = sentence_len
            else:
                current_chunk.append(sentence)
                current_size += sentence_len
        
        # Add the last chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks
    
    @staticmethod
    def _calculate_complexity(text: str) -> float:
        """
        Calculate text complexity based on lexical density.
        Returns a score between 0 and 1 (higher = more complex).
        """
        words = re.findall(r'\b\w+\b', text.lower())
        if not words:
            return 0.0
        
        unique_words = set(words)
        lexical_density = len(unique_words) / len(words)
        
        # Normalize (assume max lexical density of 0.8)
        return min(1.0, lexical_density / 0.8)
    
    @staticmethod
    def get_chunk_statistics(chunks: List[str]) -> Dict[str, any]:
        """
        Calculate comprehensive statistics about a list of chunks.
        
        Args:
            chunks: List of text chunks.
            
        Returns:
            Dictionary containing statistics.
        """
        if not chunks:
            return {
                "total_chunks": 0,
                "avg_chunk_size": 0,
                "min_chunk_size": 0,
                "max_chunk_size": 0,
                "total_characters": 0,
                "std_deviation": 0,
                "size_consistency": 0
            }
        
        chunk_sizes = [len(chunk) for chunk in chunks]
        avg_size = sum(chunk_sizes) / len(chunk_sizes)
        
        # Calculate standard deviation
        variance = sum((size - avg_size) ** 2 for size in chunk_sizes) / len(chunk_sizes)
        std_dev = variance ** 0.5
        
        # Calculate size consistency (0-1, where 1 is perfectly consistent)
        size_consistency = 1 - (std_dev / max(1, avg_size))
        
        return {
            "total_chunks": len(chunks),
            "avg_chunk_size": avg_size,
            "min_chunk_size": min(chunk_sizes),
            "max_chunk_size": max(chunk_sizes),
            "total_characters": sum(chunk_sizes),
            "std_deviation": std_dev,
            "size_consistency": max(0, size_consistency)
        }
    
    @staticmethod
    def analyze_chunk_quality(chunks: List[str]) -> Dict[str, any]:
        """
        Analyze the quality of chunks based on various metrics.
        
        Args:
            chunks: List of text chunks.
            
        Returns:
            Dictionary with quality metrics.
        """
        if not chunks:
            return {"coherence_score": 0, "boundary_score": 0, "avg_complexity": 0}
        
        # Calculate coherence (complete sentences)
        incomplete_boundaries = 0
        for chunk in chunks:
            if chunk:
                # Check if chunk starts with lowercase (likely incomplete)
                if chunk[0].islower():
                    incomplete_boundaries += 1
                # Check if chunk ends without proper punctuation
                if not re.search(r'[.!?]\s*$', chunk):
                    incomplete_boundaries += 1
        
        max_boundaries = len(chunks) * 2
        coherence_score = 1 - (incomplete_boundaries / max(1, max_boundaries))
        
        # Calculate average complexity
        complexities = [ChunkingEngine._calculate_complexity(chunk) for chunk in chunks]
        avg_complexity = sum(complexities) / len(complexities) if complexities else 0
        
        return {
            "coherence_score": coherence_score,
            "boundary_score": 1 - (incomplete_boundaries / max(1, max_boundaries)),
            "avg_complexity": avg_complexity,
            "complexity_variance": max(complexities) - min(complexities) if complexities else 0
        }
    
    @staticmethod
    def get_strategy_info(strategy: str) -> Dict[str, str]:
        """
        Get information about a specific chunking strategy.
        
        Args:
            strategy: The strategy key.
            
        Returns:
            Dictionary with strategy information.
        """
        return {
            "name": strategy,
            "description": ChunkingEngine.STRATEGY_DESCRIPTIONS.get(strategy, "No description available"),
            "recommended_size": ChunkingEngine._get_recommended_size(strategy),
            "recommended_overlap": ChunkingEngine._get_recommended_overlap(strategy)
        }
    
    @staticmethod
    def _get_recommended_size(strategy: str) -> str:
        """Get recommended chunk size for a strategy."""
        recommendations = {
            "fixed": "500-1000 characters",
            "semantic": "400-800 characters",
            "recursive": "400-800 characters",
            "character": "500-1000 characters",
            "markdown": "500-1000 characters",
            "adaptive": "300-1000 characters (auto-adjusted)",
            "sentence": "400-600 characters"
        }
        return recommendations.get(strategy, "500 characters")
    
    @staticmethod
    def _get_recommended_overlap(strategy: str) -> str:
        """Get recommended overlap for a strategy."""
        recommendations = {
            "fixed": "50-100 characters (10-20%)",
            "semantic": "50-100 characters",
            "recursive": "50-100 characters",
            "character": "50-100 characters",
            "markdown": "50-100 characters",
            "adaptive": "30-150 characters (auto-adjusted)",
            "sentence": "0-50 characters (minimal)"
        }
        return recommendations.get(strategy, "50 characters")
    
