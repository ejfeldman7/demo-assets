"""
Databricks Chunking Strategy Playground
Main Streamlit Application
"""

import streamlit as st
from data_loader import DataLoader
from chunking_engine import ChunkingEngine
from viz_utils import ChunkVisualizer
from retrieval_engine import RetrievalEngine, RetrievalMetrics


# Page configuration
st.set_page_config(
    page_title="Databricks Chunking Playground",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'data_loader' not in st.session_state:
    st.session_state.data_loader = DataLoader()

if 'loaded_text' not in st.session_state:
    st.session_state.loaded_text = None

if 'current_file' not in st.session_state:
    st.session_state.current_file = None

if 'temp_file_path' not in st.session_state:
    st.session_state.temp_file_path = None

if 'chunks_a' not in st.session_state:
    st.session_state.chunks_a = []

if 'chunks_b' not in st.session_state:
    st.session_state.chunks_b = []


def main():
    """Main application function."""
    
    # Title
    st.title("📄 Databricks Chunking Strategy Playground")
    st.markdown("""
    Test and compare different text chunking strategies for RAG applications.
    Load documents from Unity Catalog Volumes, configure chunking parameters, 
    and validate retrieval performance.
    """)
    
    # Strategy Guide
    with st.expander("📚 Chunking Strategy Guide", expanded=False):
        st.markdown("""
        ### Understanding Chunking Strategies
        
        Chunking is critical for RAG performance. Choose the right strategy for your content:
        
        #### 🔸 **Fixed-Size**
        - Simplest approach with uniform chunk sizes
        - Best for: Logs, uniform documents with consistent formatting
        - Trade-off: May cut sentences/paragraphs abruptly
        
        #### 🔸 **Semantic (Recursive)**
        - Splits at logical boundaries (paragraphs, sections)
        - Best for: Articles, reports, structured documents
        - Trade-off: Variable chunk sizes, slightly higher complexity
        
        #### 🔸 **Recursive Character**
        - Hierarchical splitting with multiple separators
        - Best for: Technical documents, code, structured text
        - Trade-off: Requires configuration of separators
        
        #### 🔸 **Character**
        - Basic newline-based splitting
        - Best for: Simple text, quick tests
        - Trade-off: Less context-aware than other methods
        
        #### 🔸 **Markdown**
        - Markdown-aware splitting that preserves structure
        - Best for: .md files, documentation, README files
        - Trade-off: Only useful for markdown content
        
        #### 🔸 **Adaptive** ⭐
        - Adjusts chunk size based on text complexity
        - Best for: Mixed-complexity documents (technical + simple)
        - Trade-off: More computational overhead
        
        #### 🔸 **Sentence-Based**
        - Splits at sentence boundaries for semantic integrity
        - Best for: Narrative content, maintaining meaning
        - Trade-off: Variable chunk sizes
        
        ### 💡 General Recommendations:
        - **General text:** 400-800 characters, 10-20% overlap
        - **Code/Technical:** 100-200 characters, 15-25% overlap  
        - **Narrative:** 500-1000 characters to preserve context
        - **Start simple** (Fixed or Recursive) then experiment with advanced strategies
        
        *Based on "The Ultimate Guide to Chunking Strategies for RAG Applications with Databricks"*
        """)
    
    # Sidebar - Volume Explorer
    with st.sidebar:
        st.header("🗂️ Unity Catalog Explorer")
        
        # Phase 1: Catalog Selection
        catalogs = st.session_state.data_loader.list_catalogs()
        
        if not catalogs:
            st.warning("No catalogs found. Please check your permissions.")
            return
        
        selected_catalog = st.selectbox(
            "Select Catalog",
            options=catalogs,
            key="selected_catalog"
        )
        
        # Phase 2: Schema Selection
        if selected_catalog:
            schemas = st.session_state.data_loader.list_schemas(selected_catalog)
            
            if not schemas:
                st.warning(f"No schemas found in catalog '{selected_catalog}'.")
                return
            
            selected_schema = st.selectbox(
                "Select Schema",
                options=schemas,
                key="selected_schema"
            )
            
            # Phase 3: Volume Selection
            if selected_schema:
                volumes = st.session_state.data_loader.list_volumes(
                    selected_catalog,
                    selected_schema
                )
                
                if not volumes:
                    st.warning(f"No volumes found in schema '{selected_schema}'.")
                    return
                
                selected_volume = st.selectbox(
                    "Select Volume",
                    options=volumes,
                    key="selected_volume"
                )
                
                # Phase 4: File Selection
                if selected_volume:
                    files = st.session_state.data_loader.list_files(
                        selected_catalog,
                        selected_schema,
                        selected_volume
                    )
                    
                    if not files:
                        st.warning(f"No supported files found in volume '{selected_volume}'.")
                        st.info("Supported file types: PDF, TXT, MD")
                        return
                    
                    # Create file display names
                    file_options = {f"{file['name']} ({file['extension']})": file for file in files}
                    
                    selected_file_name = st.selectbox(
                        "Select File",
                        options=list(file_options.keys()),
                        key="selected_file"
                    )
                    
                    if selected_file_name:
                        selected_file = file_options[selected_file_name]
                        
                        # Load button
                        if st.button("📥 Load File", type="primary"):
                            with st.spinner("Downloading and extracting text..."):
                                # Clean up previous temp file
                                if st.session_state.temp_file_path:
                                    st.session_state.data_loader.cleanup_temp_file(
                                        st.session_state.temp_file_path
                                    )
                                
                                # Download file
                                temp_path = st.session_state.data_loader.download_file(
                                    selected_file['path']
                                )
                                
                                if temp_path:
                                    # Extract text
                                    text = st.session_state.data_loader.extract_text_from_file(
                                        temp_path,
                                        selected_file['extension']
                                    )
                                    
                                    if text:
                                        st.session_state.loaded_text = text
                                        st.session_state.current_file = selected_file['name']
                                        st.session_state.temp_file_path = temp_path
                                        st.success(f"✅ Loaded {len(text)} characters from {selected_file['name']}")
                                    else:
                                        st.error("Failed to extract text from file.")
                                else:
                                    st.error("Failed to download file.")
        
        # Display loaded file info
        if st.session_state.current_file:
            st.divider()
            st.info(f"**Current File:** {st.session_state.current_file}")
            st.caption(f"Characters: {len(st.session_state.loaded_text):,}")
    
    # Main content area
    if st.session_state.loaded_text:
        # Show text preview
        with st.expander("📄 Document Preview (First 1000 characters)"):
            st.text(st.session_state.loaded_text[:1000] + "...")
        
        st.divider()
        
        # Chunking Configuration
        st.header("⚙️ Configure Chunking Strategies")
        
        st.info("💡 **Tip:** Different strategies work better for different content types. Experiment to find what works best!")
        
        col1, col2 = st.columns(2)
        
        # Strategy A Configuration
        with col1:
            st.subheader("Strategy A")
            
            strategy_a = st.selectbox(
                "Chunking Method",
                options=list(ChunkingEngine.STRATEGIES.keys()),
                key="strategy_a_method",
                help="Select a chunking strategy for side-by-side comparison"
            )
            
            # Show strategy description
            strategy_a_key = ChunkingEngine.STRATEGIES[strategy_a]
            st.caption(ChunkingEngine.STRATEGY_DESCRIPTIONS.get(strategy_a_key, ""))
            
            # Show recommendations in expander
            with st.expander("📖 Strategy Recommendations"):
                info = ChunkingEngine.get_strategy_info(strategy_a_key)
                st.write(f"**Recommended Size:** {info['recommended_size']}")
                st.write(f"**Recommended Overlap:** {info['recommended_overlap']}")
            
            # Adaptive strategy has different behavior
            if strategy_a_key == "adaptive":
                st.info("🎯 **Adaptive mode:** Chunk size will be auto-adjusted between min/max based on text complexity")
                st.caption("⚠️ Note: Uses NLTK for sentence analysis. If unavailable, falls back to semantic chunking.")
                chunk_size_a = st.slider(
                    "Target Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=500,
                    step=50,
                    key="chunk_size_a",
                    help="Target size - will be adjusted automatically based on text complexity"
                )
                st.caption("↪️ Actual range: ~60%-150% of target (simpler text = larger chunks, complex text = smaller)")
            
            # Sentence-based has different behavior
            elif strategy_a_key == "sentence":
                st.info("📝 **Sentence mode:** Chunks respect sentence boundaries (no mid-sentence cuts)")
                st.caption("⚠️ Note: Uses NLTK for sentence detection. If unavailable, falls back to regex-based splitting.")
                chunk_size_a = st.slider(
                    "Target Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=500,
                    step=50,
                    key="chunk_size_a",
                    help="Target size - actual size varies to keep sentences intact"
                )
                st.caption("↪️ Actual chunks may be smaller or larger to respect sentence boundaries")
            
            # All other strategies
            else:
                chunk_size_a = st.slider(
                    "Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=500,
                    step=50,
                    key="chunk_size_a",
                    help="Target size for each chunk in characters"
                )
            
            # Overlap control - adaptive and sentence-based handle differently
            if strategy_a_key in ["adaptive", "sentence"]:
                chunk_overlap_a = st.slider(
                    "Overlap (characters)",
                    min_value=0,
                    max_value=500,
                    value=30 if strategy_a_key == "sentence" else 50,
                    step=10,
                    key="chunk_overlap_a",
                    help="Overlap is auto-adjusted for adaptive; minimal needed for sentence-based"
                )
                if strategy_a_key == "adaptive":
                    st.caption("↪️ Overlap will be adjusted based on chunk complexity")
            else:
                chunk_overlap_a = st.slider(
                    "Chunk Overlap (characters)",
                    min_value=0,
                    max_value=500,
                    value=50,
                    step=10,
                    key="chunk_overlap_a",
                    help="Number of characters to overlap between chunks (prevents context loss)"
                )
        
        # Strategy B Configuration
        with col2:
            st.subheader("Strategy B")
            
            strategy_b = st.selectbox(
                "Chunking Method",
                options=list(ChunkingEngine.STRATEGIES.keys()),
                key="strategy_b_method",
                help="Select a different strategy to compare"
            )
            
            # Show strategy description
            strategy_b_key = ChunkingEngine.STRATEGIES[strategy_b]
            st.caption(ChunkingEngine.STRATEGY_DESCRIPTIONS.get(strategy_b_key, ""))
            
            # Show recommendations in expander
            with st.expander("📖 Strategy Recommendations"):
                info = ChunkingEngine.get_strategy_info(strategy_b_key)
                st.write(f"**Recommended Size:** {info['recommended_size']}")
                st.write(f"**Recommended Overlap:** {info['recommended_overlap']}")
            
            # Adaptive strategy has different behavior
            if strategy_b_key == "adaptive":
                st.info("🎯 **Adaptive mode:** Chunk size will be auto-adjusted between min/max based on text complexity")
                st.caption("⚠️ Note: Uses NLTK for sentence analysis. If unavailable, falls back to semantic chunking.")
                chunk_size_b = st.slider(
                    "Target Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=800,
                    step=50,
                    key="chunk_size_b",
                    help="Target size - will be adjusted automatically based on text complexity"
                )
                st.caption("↪️ Actual range: ~60%-150% of target (simpler text = larger chunks, complex text = smaller)")
            
            # Sentence-based has different behavior
            elif strategy_b_key == "sentence":
                st.info("📝 **Sentence mode:** Chunks respect sentence boundaries (no mid-sentence cuts)")
                st.caption("⚠️ Note: Uses NLTK for sentence detection. If unavailable, falls back to regex-based splitting.")
                chunk_size_b = st.slider(
                    "Target Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=800,
                    step=50,
                    key="chunk_size_b",
                    help="Target size - actual size varies to keep sentences intact"
                )
                st.caption("↪️ Actual chunks may be smaller or larger to respect sentence boundaries")
            
            # All other strategies
            else:
                chunk_size_b = st.slider(
                    "Chunk Size (characters)",
                    min_value=100,
                    max_value=2000,
                    value=800,
                    step=50,
                    key="chunk_size_b",
                    help="Target size for each chunk in characters"
                )
            
            # Overlap control - adaptive and sentence-based handle differently
            if strategy_b_key in ["adaptive", "sentence"]:
                chunk_overlap_b = st.slider(
                    "Overlap (characters)",
                    min_value=0,
                    max_value=500,
                    value=30 if strategy_b_key == "sentence" else 100,
                    step=10,
                    key="chunk_overlap_b",
                    help="Overlap is auto-adjusted for adaptive; minimal needed for sentence-based"
                )
                if strategy_b_key == "adaptive":
                    st.caption("↪️ Overlap will be adjusted based on chunk complexity")
            else:
                chunk_overlap_b = st.slider(
                    "Chunk Overlap (characters)",
                    min_value=0,
                    max_value=500,
                    value=100,
                    step=10,
                    key="chunk_overlap_b",
                    help="Number of characters to overlap between chunks"
                )
        
        # Generate Chunks Button
        if st.button("🔄 Generate Chunks", type="primary"):
            with st.spinner("Generating chunks..."):
                # Generate chunks for Strategy A
                st.session_state.chunks_a = ChunkingEngine.split_text(
                    text=st.session_state.loaded_text,
                    strategy=ChunkingEngine.STRATEGIES[strategy_a],
                    chunk_size=chunk_size_a,
                    chunk_overlap=chunk_overlap_a
                )
                
                # Generate chunks for Strategy B
                st.session_state.chunks_b = ChunkingEngine.split_text(
                    text=st.session_state.loaded_text,
                    strategy=ChunkingEngine.STRATEGIES[strategy_b],
                    chunk_size=chunk_size_b,
                    chunk_overlap=chunk_overlap_b
                )
                
                st.success("✅ Chunks generated successfully!")
        
        # Display Chunks if available
        if st.session_state.chunks_a and st.session_state.chunks_b:
            st.divider()
            st.header("📊 Chunking Results")
            
            col1, col2 = st.columns(2)
            
            # Strategy A Results
            with col1:
                st.markdown("### Strategy A Results")
                stats_a = ChunkingEngine.get_chunk_statistics(st.session_state.chunks_a)
                quality_a = ChunkingEngine.analyze_chunk_quality(st.session_state.chunks_a)
                
                # Display basic statistics
                ChunkVisualizer.display_statistics(stats_a)
                
                # Display quality metrics
                with st.expander("📊 Quality Metrics"):
                    col_a1, col_a2 = st.columns(2)
                    with col_a1:
                        st.metric("Coherence Score", f"{quality_a['coherence_score']:.2f}")
                        st.caption("1.0 = perfect sentence boundaries")
                    with col_a2:
                        st.metric("Avg Complexity", f"{quality_a['avg_complexity']:.2f}")
                        st.caption("0-1 scale, higher = more complex")
                    
                    st.metric("Size Consistency", f"{stats_a['size_consistency']:.2f}")
                    st.caption("1.0 = perfectly uniform chunks")
                
                st.markdown("#### Chunk Visualization")
                
                # Handle case where there are very few chunks
                total_chunks_a = len(st.session_state.chunks_a)
                if total_chunks_a > 1:
                    max_display = st.slider(
                        "Max chunks to display",
                        min_value=1,
                        max_value=min(20, total_chunks_a),
                        value=min(5, total_chunks_a),
                        key="max_display_a"
                    )
                else:
                    max_display = total_chunks_a
                    st.caption(f"Displaying {total_chunks_a} chunk(s)")
                
                ChunkVisualizer.visualize_chunks(
                    st.session_state.chunks_a,
                    max_display_chunks=max_display
                )
            
            # Strategy B Results
            with col2:
                st.markdown("### Strategy B Results")
                stats_b = ChunkingEngine.get_chunk_statistics(st.session_state.chunks_b)
                quality_b = ChunkingEngine.analyze_chunk_quality(st.session_state.chunks_b)
                
                # Display basic statistics
                ChunkVisualizer.display_statistics(stats_b)
                
                # Display quality metrics
                with st.expander("📊 Quality Metrics"):
                    col_b1, col_b2 = st.columns(2)
                    with col_b1:
                        st.metric("Coherence Score", f"{quality_b['coherence_score']:.2f}")
                        st.caption("1.0 = perfect sentence boundaries")
                    with col_b2:
                        st.metric("Avg Complexity", f"{quality_b['avg_complexity']:.2f}")
                        st.caption("0-1 scale, higher = more complex")
                    
                    st.metric("Size Consistency", f"{stats_b['size_consistency']:.2f}")
                    st.caption("1.0 = perfectly uniform chunks")
                
                st.markdown("#### Chunk Visualization")
                
                # Handle case where there are very few chunks
                total_chunks_b = len(st.session_state.chunks_b)
                if total_chunks_b > 1:
                    max_display = st.slider(
                        "Max chunks to display",
                        min_value=1,
                        max_value=min(20, total_chunks_b),
                        value=min(5, total_chunks_b),
                        key="max_display_b"
                    )
                else:
                    max_display = total_chunks_b
                    st.caption(f"Displaying {total_chunks_b} chunk(s)")
                
                ChunkVisualizer.visualize_chunks(
                    st.session_state.chunks_b,
                    max_display_chunks=max_display
                )
            
            # Retrieval Testing Section
            st.divider()
            st.header("🔍 Retrieval Testing (RAG Validation)")
            
            st.markdown("""
            Test how well each chunking strategy performs for retrieval.
            Enter a query and see which chunks are retrieved from each strategy.
            """)
            
            # Embedding endpoint configuration
            embedding_endpoint = st.text_input(
                "Databricks Embedding Endpoint",
                value="databricks-bge-large-en",
                help="Enter the name of your Databricks Model Serving embedding endpoint"
            )
            
            # Query input
            query = st.text_input(
                "Test Query",
                placeholder="e.g., What is the main topic of this document?",
                help="Enter a question or search query"
            )
            
            # Number of results
            top_k = st.slider(
                "Number of results to retrieve",
                min_value=1,
                max_value=10,
                value=3
            )
            
            # Retrieve button
            if st.button("🚀 Run Retrieval Test", type="primary"):
                if not query:
                    st.warning("Please enter a test query.")
                elif not embedding_endpoint:
                    st.warning("Please enter an embedding endpoint name.")
                else:
                    with st.spinner("🔄 Waking up embedding model and running retrieval..."):
                        try:
                            # Initialize retrieval engine
                            retrieval_engine = RetrievalEngine(embedding_endpoint)
                            
                            # Compare retrieval
                            results_a, results_b = retrieval_engine.compare_retrieval(
                                query=query,
                                chunks_a=st.session_state.chunks_a,
                                chunks_b=st.session_state.chunks_b,
                                k=top_k
                            )
                            
                            # Display results
                            st.success("✅ Retrieval complete!")
                            
                            col1, col2 = st.columns(2)
                            
                            with col1:
                                st.markdown("### Strategy A Results")
                                st.markdown(f"**Average Distance:** {RetrievalMetrics.calculate_average_distance(results_a):.4f}")
                                
                                for result in results_a:
                                    with st.expander(f"Rank {result['rank']} - Distance: {result['distance']:.4f}"):
                                        st.markdown(f"**Chunk Index:** {result['chunk_index']}")
                                        st.markdown(f"**Similarity Score:** {result['similarity']:.4f}")
                                        st.text(result['chunk'])
                            
                            with col2:
                                st.markdown("### Strategy B Results")
                                st.markdown(f"**Average Distance:** {RetrievalMetrics.calculate_average_distance(results_b):.4f}")
                                
                                for result in results_b:
                                    with st.expander(f"Rank {result['rank']} - Distance: {result['distance']:.4f}"):
                                        st.markdown(f"**Chunk Index:** {result['chunk_index']}")
                                        st.markdown(f"**Similarity Score:** {result['similarity']:.4f}")
                                        st.text(result['chunk'])
                        
                        except Exception as e:
                            st.error(f"Error during retrieval: {str(e)}")
                            st.info("Make sure the embedding endpoint is available and you have proper permissions.")
    
    else:
        # No file loaded yet
        st.info("👈 Please select and load a file from the sidebar to begin.")


if __name__ == "__main__":
    main()

