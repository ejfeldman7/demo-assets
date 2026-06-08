"""
Visualization Utilities Module
Provides functions for visualizing text chunks in Streamlit.
"""

from typing import List
import streamlit as st


class ChunkVisualizer:
    """Handles visualization of text chunks."""
    
    # Soft pastel colors for readability (as specified in PRD)
    COLORS = ["#E8F8F5", "#FEF9E7", "#F4ECF7", "#FDEBD0"]
    
    @staticmethod
    def visualize_chunks(chunks: List[str], max_display_chunks: int = None):
        """
        Visualize text chunks using colored backgrounds with HTML/CSS.
        
        Args:
            chunks: List of text chunks to visualize.
            max_display_chunks: Maximum number of chunks to display (None = all).
        """
        if not chunks:
            st.warning("No chunks to display.")
            return
        
        # Limit displayed chunks if specified
        display_chunks = chunks[:max_display_chunks] if max_display_chunks else chunks
        
        # Build HTML with colored backgrounds
        html_parts = []
        html_parts.append('<div style="line-height: 1.8; font-family: monospace; font-size: 14px;">')
        
        for i, chunk in enumerate(display_chunks):
            color = ChunkVisualizer.COLORS[i % len(ChunkVisualizer.COLORS)]
            
            # Escape HTML special characters to prevent parsing issues
            chunk_escaped = chunk.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            # Add chunk with background color
            html_parts.append(
                f'<span style="background-color: {color}; padding: 2px 4px; border-radius: 3px; '
                f'margin-right: 2px;">{chunk_escaped}</span>'
            )
            
            # Add a separator between chunks
            if i < len(display_chunks) - 1:
                html_parts.append(
                    f'<span style="color: #666; font-weight: bold; margin: 0 8px;">⟨Chunk {i+2}⟩</span>'
                )
        
        html_parts.append('</div>')
        
        # Display using Streamlit's HTML renderer
        st.markdown(''.join(html_parts), unsafe_allow_html=True)
        
        # Show truncation message if applicable
        if max_display_chunks and len(chunks) > max_display_chunks:
            st.info(f"Showing first {max_display_chunks} of {len(chunks)} total chunks.")
    
    @staticmethod
    def display_statistics(statistics: dict, title: str = "Statistics"):
        """
        Display chunk statistics in a formatted metrics layout.
        
        Args:
            statistics: Dictionary containing chunk statistics.
            title: Title for the statistics section.
        """
        st.subheader(title)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("Total Chunks", statistics.get("total_chunks", 0))
        
        with col2:
            avg_size = statistics.get("avg_chunk_size", 0)
            st.metric("Avg Chunk Size", f"{avg_size:.0f} chars")
        
        with col3:
            max_size = statistics.get("max_chunk_size", 0)
            st.metric("Max Chunk Size", f"{max_size} chars")
        
        # Additional details in an expander
        with st.expander("More Details"):
            st.write(f"**Min Chunk Size:** {statistics.get('min_chunk_size', 0)} characters")
            st.write(f"**Total Characters:** {statistics.get('total_characters', 0):,} characters")
    

