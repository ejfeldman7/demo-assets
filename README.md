# 📄 Databricks Chunking Strategy Playground

An interactive Streamlit application for AI engineers to test, compare, and validate text chunking strategies for RAG (Retrieval-Augmented Generation) applications.

![Platform](https://img.shields.io/badge/platform-Databricks-red)
![Framework](https://img.shields.io/badge/framework-Streamlit-FF4B4B)
![Python](https://img.shields.io/badge/python-3.9+-blue)

## 🎯 What Does This Do?

The Chunking Playground helps you answer critical questions before building production RAG systems:

- **Which chunking strategy works best for my documents?**
- **What chunk size gives optimal retrieval performance?**
- **How much overlap should I use?**
- **Can I visualize how my text is being split?**

## ✨ Key Features

### 📂 Unity Catalog Integration
- Browse catalogs, schemas, and volumes directly
- Load PDF, TXT, and MD files seamlessly
- No manual file uploads needed

### ⚙️ Multiple Chunking Strategies
- **Recursive Character Splitting**: Smart splitting on natural boundaries
- **Character Splitting**: Simple, fast splitting
- **Markdown Splitting**: Structure-aware for documentation

### 👁️ Visual Chunk Display
- Color-coded visualization of chunk boundaries
- Side-by-side strategy comparison
- Real-time statistics (count, size, distribution)

### 🔍 RAG Validation
- Test retrieval with actual Databricks embeddings
- Compare which strategy retrieves better context
- See distance/similarity scores for each result

## 🚀 Quick Start

Get started in 4 steps:

1. **Configure your Databricks CLI** with your workspace:
   ```bash
   databricks auth login --host https://your-workspace.cloud.databricks.com
   ```

2. **Deploy to Databricks**:
   ```bash
   cd chunking_playground
   databricks apps deploy chunking_playground
   ```

3. **Grant permissions** to your app's service principal (note the name from deployment output):
   ```sql
   GRANT USE CATALOG ON CATALOG `your_catalog` TO `app-xxxxx chunking-playground`;
   GRANT USE SCHEMA ON SCHEMA `your_catalog`.`your_schema` TO `app-xxxxx chunking-playground`;
   GRANT READ VOLUME ON VOLUME `your_catalog`.`your_schema`.`your_volume` TO `app-xxxxx chunking-playground`;
   ```

4. **Open in your workspace** → Apps → chunking-playground

👉 See [DEPLOYMENT.md](DEPLOYMENT.md) for complete deployment instructions.

## 📁 Project Structure

```
chunking_playground/
├── src/
│   ├── app.py                    # Main Streamlit application
│   ├── data_loader.py            # Unity Catalog integration
│   ├── chunking_engine.py        # Text splitting strategies
│   ├── viz_utils.py              # Visualization components
│   ├── retrieval_engine.py       # RAG testing with FAISS
│   ├── requirements.txt          # Python dependencies
│   ├── README.md                 # Detailed documentation
│   └── .streamlit/
│       └── config.toml           # Streamlit configuration
├── instructions/
│   └── overview.md               # Product Requirements Doc (PRD)
├── databricks.yml                # Databricks Apps deployment config
├── QUICKSTART.md                 # Quick start guide
├── .gitignore                    # Git ignore rules
└── README.md                     # This file
```

## 🎓 How It Works

### Phase 1: Load Your Document
Navigate Unity Catalog to select and load a document from your volumes.

### Phase 2: Configure Strategies
Set up two different chunking strategies with custom parameters:
- Chunking method (Recursive, Character, Markdown)
- Chunk size (100-2000 characters)
- Chunk overlap (0-500 characters)

### Phase 3: Visualize Results
See exactly how your text is being split:
- Color-coded chunk boundaries
- Statistics on chunk counts and sizes
- Preview of actual chunks

### Phase 4: Test Retrieval (Optional)
Validate which strategy works better:
- Enter a test query
- See which chunks are retrieved
- Compare distance/similarity scores

## 🎯 Use Cases

### For AI Engineers
- Validate chunking before building vector indexes
- Compare strategies on your actual documents
- Optimize chunk parameters for specific queries

### For Data Scientists
- Understand how chunking affects retrieval quality
- Experiment with different configurations
- Gather metrics for documentation

### For MLOps Teams
- Test production chunking strategies
- Validate changes before deployment
- Share results with stakeholders

## 📚 Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - Get started in 5 minutes
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide with permissions
- **[CHUNKING_STRATEGIES.md](CHUNKING_STRATEGIES.md)** - Guide to all chunking strategies
- **[src/README.md](src/README.md)** - Technical documentation

## 🛠️ Technical Stack

| Component | Technology |
|-----------|-----------|
| **Platform** | Databricks Apps (Serverless) |
| **Framework** | Streamlit |
| **Data Source** | Unity Catalog Volumes |
| **Embeddings** | Databricks Model Serving |
| **Vector Search** | FAISS (in-memory) |
| **Text Processing** | LangChain |

## 📦 Dependencies

Core libraries:
- `streamlit` - Web UI framework
- `databricks-sdk` - Unity Catalog & Model Serving access
- `langchain` - Text splitting utilities
- `pypdf` - PDF text extraction
- `faiss-cpu` - Vector similarity search

See [src/requirements.txt](src/requirements.txt) for complete list.

## 🔒 Security & Permissions

The app requires:
- ✅ READ access to Unity Catalog Volumes (via service principal)
- ✅ EXECUTE access to Model Serving endpoints (for retrieval testing)
- ✅ Valid Databricks workspace authentication

Authentication is handled automatically when deployed as a Databricks App. After deployment, you'll need to grant the app's service principal access to your Unity Catalog volumes. See [QUICKSTART.md](QUICKSTART.md) for details.

## 🤝 Contributing

This is a playground application designed for experimentation. Feel free to:
- Add new chunking strategies
- Enhance visualization methods
- Add additional metrics
- Improve the UI/UX

## 📝 License

Built for Databricks environments. Use freely within your organization.

## 🙏 Acknowledgments

Built according to the [Product Requirements Document](instructions/overview.md) with the following goals:
- Enable rapid experimentation with chunking strategies
- Provide visual feedback for better understanding
- Validate retrieval performance before production
- Make RAG development more interactive and data-driven

---

**Ready to optimize your RAG pipeline? Start chunking! 🚀**

Questions? Check the [QUICKSTART](QUICKSTART.md) or [full documentation](src/README.md).



