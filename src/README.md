# Databricks Chunking Strategy Playground

An interactive Streamlit application for testing and comparing different text chunking strategies for RAG (Retrieval-Augmented Generation) applications. Built to run on Databricks Apps (Serverless).

## 🎯 Features

- **Unity Catalog Integration**: Browse and load documents directly from Unity Catalog Volumes
- **Multiple Chunking Strategies**: Compare different text splitting methods side-by-side
  - Recursive Character Splitting
  - Character Splitting
  - Markdown-aware Splitting
- **Visual Chunk Display**: Color-coded visualization of how text is split into chunks
- **Statistics & Metrics**: Detailed analytics on chunk sizes and distributions
- **RAG Validation**: Test retrieval performance using Databricks Model Serving embeddings
- **Side-by-Side Comparison**: Evaluate which strategy works best for your use case

## 📋 Prerequisites

- Databricks Workspace with Unity Catalog enabled
- Access to a Unity Catalog Volume with documents (PDF, TXT, or MD files)
- Databricks Model Serving endpoint for embeddings (e.g., `databricks-bge-large-en`)
- Python 3.9 or higher

## 🚀 Getting Started

### Installation

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Set Up Authentication**:
   
   The app uses the Databricks SDK which automatically authenticates using your workspace credentials. When deploying as a Databricks App, authentication is handled automatically by the service principal.
   
   For local development, ensure you have configured your Databricks CLI or set the following environment variables:
   ```bash
   export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
   export DATABRICKS_TOKEN="your-token-here"
   ```

### Running the Application

#### On Databricks Apps (Production)

1. **Deploy as Databricks App**:
   ```bash
   databricks apps deploy chunking_playground
   ```

2. **Access the App**:
   Navigate to your Databricks workspace and find the app under "Apps"

#### Local Development

1. **Run with Streamlit**:
   ```bash
   streamlit run app.py
   ```

2. **Open in Browser**:
   The app will open automatically at `http://localhost:8501`

## 📖 Usage Guide

### Step 1: Load a Document

1. Use the sidebar to navigate Unity Catalog:
   - Select a **Catalog**
   - Select a **Schema**
   - Select a **Volume**
   - Choose a **File** (PDF, TXT, or MD)
2. Click **"Load File"** to download and extract text

### Step 2: Configure Chunking Strategies

Configure two strategies side-by-side:

**Strategy A** (Left Panel):
- Choose chunking method (Recursive Character, Character, Markdown)
- Set chunk size (100-2000 characters)
- Set chunk overlap (0-500 characters)

**Strategy B** (Right Panel):
- Configure with different parameters to compare

### Step 3: Generate Chunks

Click **"Generate Chunks"** to split the text using both strategies.

### Step 4: Analyze Results

Review the visualization and statistics:
- **Total Chunks**: How many chunks were created
- **Average Chunk Size**: Mean size across all chunks
- **Max Chunk Size**: Largest chunk in the set
- **Visual Display**: Color-coded chunks showing boundaries

### Step 5: Test Retrieval (Optional)

Validate which strategy works better for retrieval:

1. Enter your **Embedding Endpoint** name (e.g., `databricks-bge-large-en`)
2. Type a **Test Query** (e.g., "What is the main topic?")
3. Set number of results to retrieve (1-10)
4. Click **"Run Retrieval Test"**

The app will:
- Generate embeddings for all chunks
- Create FAISS indices for both strategies
- Retrieve top-k most similar chunks
- Display distance scores for comparison

## 🏗️ Architecture

### Project Structure

```
src/
├── app.py                  # Main Streamlit application
├── data_loader.py          # Unity Catalog Volume interaction
├── chunking_engine.py      # Text splitting strategies
├── viz_utils.py            # Visualization helpers
├── retrieval_engine.py     # RAG testing with FAISS
├── requirements.txt        # Python dependencies
└── README.md              # This file
```

### Key Components

1. **DataLoader**: Handles Unity Catalog interactions
   - Lists catalogs, schemas, volumes, and files
   - Downloads files to temporary storage
   - Extracts text from PDFs and text files

2. **ChunkingEngine**: Implements text splitting
   - Multiple LangChain-based strategies
   - Configurable chunk size and overlap
   - Statistics calculation

3. **ChunkVisualizer**: Displays chunks visually
   - Color-coded annotations
   - Side-by-side comparisons
   - Statistics panels

4. **RetrievalEngine**: Tests RAG performance
   - Databricks Model Serving integration
   - FAISS in-memory indexing
   - Similarity search and ranking

## ⚙️ Configuration

### Chunking Strategies

**Recursive Character Splitter**:
- Best for general text documents
- Tries to split on natural boundaries (paragraphs, sentences, words)
- Most versatile option

**Character Splitter**:
- Simple splitting at newline characters
- Good for structured text with clear line breaks
- Faster but less sophisticated

**Markdown Splitter**:
- Markdown-aware splitting
- Preserves document structure
- Best for .md files and documentation

### Best Practices

1. **Chunk Size**: 
   - Start with 500-1000 characters
   - Smaller chunks (200-500) for precise retrieval
   - Larger chunks (1000-2000) for more context

2. **Chunk Overlap**:
   - Typically 10-20% of chunk size
   - Prevents context loss at boundaries
   - 50-100 characters is a good starting point

3. **File Size Limits**:
   - PDFs are limited to first 20 pages by default
   - Prevents memory issues during testing
   - Adjust in `data_loader.py` if needed

## 🔒 Security & Permissions

- The app inherits authentication from the Databricks workspace
- Requires READ permissions on Unity Catalog Volumes
- Requires access to Model Serving endpoints for retrieval testing
- All file processing happens in ephemeral storage (no persistent data)

## 🐛 Troubleshooting

### "No catalogs found"
- Check Unity Catalog permissions
- Verify authentication is configured correctly

### "Failed to download file"
- Ensure you have READ access to the Volume
- Check if the file path is correct

### "Error during retrieval"
- Verify embedding endpoint name is correct
- Check Model Serving endpoint is running (not stopped)
- Ensure you have EXECUTE permission on the endpoint
- Wait for "cold start" if endpoint was inactive

### Streamlit memory issues
- Reduce the number of chunks displayed
- Process smaller documents
- Adjust max_pages setting for PDFs

## 📚 Dependencies

- `streamlit`: Web UI framework
- `databricks-sdk`: Unity Catalog and Model Serving access
- `langchain`: Text splitting utilities
- `pypdf`: PDF text extraction
- `st-annotated-text`: Chunk visualization
- `faiss-cpu`: Vector similarity search
- `numpy`: Numerical operations

## 🤝 Contributing

This is a playground application for testing chunking strategies. Feel free to:
- Add new chunking strategies
- Improve visualization methods
- Add additional metrics
- Enhance the retrieval testing

## 📝 License

This project is designed for use within Databricks environments.

## 💡 Tips & Tricks

1. **Finding the Right Strategy**:
   - Test multiple configurations on your actual documents
   - Use retrieval testing to validate performance
   - Consider your specific use case (Q&A vs. summarization)

2. **Optimizing for RAG**:
   - Balance chunk size with context needs
   - Use overlap to prevent information loss
   - Test with actual queries from your application

3. **Performance**:
   - Start with smaller documents during testing
   - Use fewer chunks for visualization (5-10)
   - Keep retrieval tests to 3-5 results for faster iteration

## 📞 Support

For issues or questions:
- Check Databricks documentation for Unity Catalog and Model Serving
- Review LangChain documentation for chunking strategies
- Consult Databricks support for platform-specific issues



