# Quick Start Guide

Get started with the Databricks Chunking Playground in 5 minutes!

## Prerequisites

✅ Databricks Workspace with Unity Catalog  
✅ Unity Catalog Volume with some documents (PDF, TXT, or MD)  
✅ Databricks CLI configured with your workspace  
✅ Admin permissions to grant Unity Catalog access  

## 🚀 Deploy to Databricks Apps

### Step 1: Configure Databricks CLI

If you haven't already, configure your Databricks CLI:

```bash
databricks auth login --host https://your-workspace.cloud.databricks.com
```

### Step 2: Deploy the App

```bash
# Navigate to the project directory
cd chunking_playground

# Deploy the app
databricks apps deploy chunking_playground
```

### Step 3: Grant Permissions

After deployment, note the **service principal name** from the output (e.g., `app-xxxxx chunking-playground`).

Grant the necessary permissions using SQL:

```sql
-- Replace with your catalog, schema, and volume names
GRANT USE CATALOG ON CATALOG `your_catalog` TO `app-xxxxx chunking-playground`;
GRANT USE SCHEMA ON SCHEMA `your_catalog`.`your_schema` TO `app-xxxxx chunking-playground`;
GRANT READ VOLUME ON VOLUME `your_catalog`.`your_schema`.`your_volume` TO `app-xxxxx chunking-playground`;
```

### Step 4: Open the App

Navigate to: **Databricks Workspace → Apps → chunking-playground**

## 🧪 Run Locally (Optional)

For development or testing:

```bash
# 1. Install dependencies
cd chunking_playground/src
pip install -r requirements.txt

# 2. Set up authentication
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export DATABRICKS_TOKEN="your-token-here"

# 3. Run the app
streamlit run app.py
```

## 📋 Setup Your Data

### 1. Prepare Your Documents

Upload some documents to a Unity Catalog Volume:

```python
# Example: Upload a PDF to your volume
dbutils.fs.cp(
    "file:/path/to/document.pdf",
    "/Volumes/your_catalog/your_schema/your_volume/document.pdf"
)
```

Supported file types: PDF, TXT, MD

### 2. Set Up Embedding Endpoint (Optional)

For retrieval testing, you'll need access to a Databricks Model Serving endpoint for embeddings:

- **Foundation Models**: Use `databricks-bge-large-en` or other available foundation models
- **Custom**: Or deploy your own embedding model

Check available endpoints:
```python
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
endpoints = w.serving_endpoints.list()
for e in endpoints:
    print(e.name)
```

## 🎯 Your First Test

1. **Load a Document**:
   - Open the sidebar
   - Navigate: Catalog → Schema → Volume → File
   - Click "Load File"

2. **Configure Strategies**:
   - Strategy A: Recursive Character, Size: 500, Overlap: 50
   - Strategy B: Recursive Character, Size: 1000, Overlap: 100

3. **Generate Chunks**:
   - Click "Generate Chunks"
   - Review the statistics and visualizations

4. **Test Retrieval** (Optional):
   - Enter embedding endpoint: `databricks-bge-large-en`
   - Enter a test query: "What is the main topic?"
   - Click "Run Retrieval Test"

## 💡 Quick Tips

- **Start Small**: Test with a short document first (2-5 pages)
- **Iterate Quickly**: Use the sliders to find optimal chunk sizes
- **Compare Methods**: Try Recursive vs Character to see the difference
- **Monitor Distance**: Lower distance = better retrieval match

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| "No catalogs found" | Verify service principal has USE CATALOG permissions |
| "Failed to download file" | Check READ VOLUME permissions on the specific volume |
| "Embedding endpoint error" | Ensure endpoint exists and is accessible |
| Slow retrieval | Wait for endpoint to warm up (30-60 seconds first time) |
| Deployment fails | Ensure Databricks CLI is configured correctly |

## 💡 Granting Additional Volume Access

To grant access to more volumes after deployment:

```sql
-- For each additional volume you want to access
GRANT USE CATALOG ON CATALOG `catalog_name` TO `app-xxxxx chunking-playground`;
GRANT USE SCHEMA ON SCHEMA `catalog_name`.`schema_name` TO `app-xxxxx chunking-playground`;
GRANT READ VOLUME ON VOLUME `catalog_name`.`schema_name`.`volume_name` TO `app-xxxxx chunking-playground`;
```

## 📚 What's Next?

- Read the full [README](README.md) for detailed documentation
- Review [CHUNKING_STRATEGIES.md](CHUNKING_STRATEGIES.md) for strategy guidance
- Explore different chunking strategies for your use case
- Test with your actual documents and queries
- Share results with your team!

---

**Happy Chunking! 📄✨**



