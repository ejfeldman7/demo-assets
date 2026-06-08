"""
Retrieval Engine Module
Implements RAG testing with FAISS and Databricks Model Serving embeddings.
"""

from typing import List, Dict, Tuple, Optional
import numpy as np
import faiss
from langchain_community.embeddings import DatabricksEmbeddings


class RetrievalEngine:
    """Handles embedding generation and retrieval testing."""
    
    def __init__(self, embedding_endpoint: str):
        """
        Initialize RetrievalEngine with a Databricks embedding endpoint.
        
        Args:
            embedding_endpoint: Name of the Databricks Model Serving endpoint
                              (e.g., 'databricks-bge-large-en').
        """
        self.embedding_endpoint = embedding_endpoint
        self.embeddings = None
        self.dimension = None
        
        # Will be set when embeddings are initialized
        self._initialize_embeddings()
    
    def _initialize_embeddings(self):
        """Initialize the Databricks embeddings client."""
        try:
            self.embeddings = DatabricksEmbeddings(
                endpoint=self.embedding_endpoint
            )
        except Exception as e:
            print(f"Error initializing embeddings: {e}")
            raise
    
    def create_index(self, chunks: List[str]) -> Tuple[faiss.IndexFlatL2, List[str]]:
        """
        Create a FAISS index from a list of text chunks.
        
        Args:
            chunks: List of text chunks to index.
            
        Returns:
            Tuple of (FAISS index, list of chunks in the same order).
        """
        if not chunks:
            raise ValueError("Cannot create index from empty chunks list.")
        
        # Generate embeddings for all chunks
        embeddings_list = self.embeddings.embed_documents(chunks)
        embeddings_array = np.array(embeddings_list, dtype=np.float32)
        
        # Store dimension for future reference
        if self.dimension is None:
            self.dimension = embeddings_array.shape[1]
        
        # Create FAISS index (L2 distance)
        index = faiss.IndexFlatL2(self.dimension)
        index.add(embeddings_array)
        
        return index, chunks
    
    def search(
        self,
        query: str,
        index: faiss.IndexFlatL2,
        chunks: List[str],
        k: int = 3
    ) -> List[Dict[str, any]]:
        """
        Search the FAISS index with a query and return top-k results.
        
        Args:
            query: The search query.
            index: The FAISS index to search.
            chunks: List of chunks corresponding to the index.
            k: Number of top results to return.
            
        Returns:
            List of dictionaries containing:
            - rank: Ranking position (1-indexed)
            - chunk: The retrieved chunk text
            - distance: L2 distance score (lower is better)
            - similarity: Normalized similarity score (higher is better)
        """
        # Generate embedding for the query
        query_embedding = self.embeddings.embed_query(query)
        query_vector = np.array([query_embedding], dtype=np.float32)
        
        # Search the index
        distances, indices = index.search(query_vector, min(k, len(chunks)))
        
        # Format results
        results = []
        for rank, (idx, distance) in enumerate(zip(indices[0], distances[0])):
            # Convert L2 distance to a similarity score
            # Using negative distance so higher is better
            similarity = -float(distance)
            
            results.append({
                "rank": rank + 1,
                "chunk": chunks[idx],
                "distance": float(distance),
                "similarity": similarity,
                "chunk_index": int(idx)
            })
        
        return results
    
    def compare_retrieval(
        self,
        query: str,
        chunks_a: List[str],
        chunks_b: List[str],
        k: int = 3
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Compare retrieval results between two chunking strategies.
        
        Args:
            query: The search query.
            chunks_a: Chunks from strategy A.
            chunks_b: Chunks from strategy B.
            k: Number of top results to return for each strategy.
            
        Returns:
            Tuple of (results_a, results_b), each containing top-k retrieval results.
        """
        # Create indices for both strategies
        index_a, chunks_a_indexed = self.create_index(chunks_a)
        index_b, chunks_b_indexed = self.create_index(chunks_b)
        
        # Search both indices
        results_a = self.search(query, index_a, chunks_a_indexed, k)
        results_b = self.search(query, index_b, chunks_b_indexed, k)
        
        return results_a, results_b


class RetrievalMetrics:
    """Calculate and display metrics for retrieval comparisons."""
    
    @staticmethod
    def calculate_average_distance(results: List[Dict[str, any]]) -> float:
        """
        Calculate the average distance of retrieval results.
        
        Args:
            results: List of retrieval results.
            
        Returns:
            Average distance score.
        """
        if not results:
            return float('inf')
        
        distances = [r['distance'] for r in results]
        return sum(distances) / len(distances)



