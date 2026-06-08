# Unity Catalog AI Image Predictions Browser

A Streamlit application for browsing and filtering AI-predicted images stored in Unity Catalog volumes, powered by Lakebase for real-time metadata sync.

## 🎯 Overview

This application provides an interactive interface for browsing AI image prediction results. It connects to Unity Catalog volumes containing images and their AI-generated predictions (labels, confidence scores, and details), using Lakebase to sync metadata to PostgreSQL for fast filtering and search capabilities.

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Streamlit     │    │    Lakebase      │    │ Unity Catalog   │
│   Web App       │◄───┤   PostgreSQL     │◄───┤   Volumes +     │
│                 │    │     Sync         │    │  AI Predictions │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

### Key Components

- **Frontend**: Streamlit application with filtering and comparison interface
- **Database**: PostgreSQL (via Lakebase) containing AI prediction metadata
- **Storage**: Unity Catalog volumes with image files
- **Authentication**: OAuth integration with Databricks workspace

## ✨ Features

### 🔍 Advanced Filtering
- **Label-based filtering**: Filter images by AI-predicted labels (e.g., "person", "car", "building")
- **Label detail filtering**: Refine by specific sub-categories within labels
- **Score range filtering**: Filter by AI confidence scores (0.0 - 1.0 range)
- **Combined filtering**: Mix and match filters for precise results

### 🖼️ Image Comparison
- **Side-by-side display**: Compare two filtered images simultaneously
- **Matplotlib visualization**: Clean, publication-ready image display
- **Metadata display**: View file paths, dimensions, and prediction details

### 🏃‍♂️ Performance
- **Fast filtering**: PostgreSQL-powered filtering for instant results
- **Efficient loading**: Unity Catalog volume integration for reliable image access
- **Responsive UI**: Streamlined interface optimized for data exploration

## 📋 Prerequisites

### Unity Catalog Setup
- Unity Catalog workspace with volumes enabled
- Image files stored in a Unity Catalog volume (e.g., `/Volumes/demos/image_app/images/`)
- AI predictions table (`image_predictions`) with the following schema:

```sql
CREATE TABLE {schema}.image_predictions (
    path TEXT,              -- Path to image file in Unity Catalog volume
    label TEXT,             -- AI-predicted label (e.g., "person", "vehicle")
    "labelDetail" TEXT,     -- Detailed sub-category 
    score DECIMAL(5,4)      -- Confidence score (0.0 - 1.0)
);
```

### Lakebase Configuration
- Lakebase instance connected to your Databricks workspace
- PostgreSQL sync configured for the `image_predictions` table
- Database credentials and connection details

## ⚙️ Installation & Setup

### 1. Environment Variables

These should all be set by adding the resource to your app in the Databricks UI. 
If you get an error shown in the app regarding them, make sure your resource is added.
For local deployment, you can work with them as environment variables. 


```bash
# Databricks Configuration
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com

# PostgreSQL Configuration (Lakebase)
PGDATABASE=your_database_name
PGUSER=your_username
PGHOST=your_postgres_host
PGPORT=5432
```

### 2. Deploy to Databricks Apps

```bash
# Clone repository
git clone <repository-url> #TODO: Move to repo
cd lakebase_image_serving #Or wherever you have these files

# Deploy using Databricks Asset Bundle
databricks bundle deploy --target dev # Or other target

# Or run locally for development
streamlit run app.py
```

### 3. Configure Database Resource

1. In Databricks Apps UI, add your Lakebase PostgreSQL database as a resource
2. This automatically provides connection environment variables to your app
3. Verify the `image_predictions` table is accessible and contains data

## 🔧 Configuration

### Database Settings (`src/config.py`)

```python
# Database configuration  
DEFAULT_SCHEMA = "example"           # Your database schema in Lakebase
TABLE_NAME = "image_predictions"     # Table with file paths and metadata
PATH_COLUMN = "path"                 # Column containing image file paths
VOLUME_BASE_PATH = "/Volumes/demos/image_app/images"  # Unity Catalog volume path containing images
```

### Connection Settings

```python
# Connection pooling
TOKEN_REFRESH_INTERVAL = 900        # OAuth token refresh (15 minutes)
CONNECTION_POOL_MIN_SIZE = 2        # Minimum database connections
CONNECTION_POOL_MAX_SIZE = 10       # Maximum database connections
```

## 🚀 Usage

### Filtering Images

1. **Choose Label**: Select from available AI prediction labels
2. **Refine with Label Detail**: Pick specific sub-categories (automatically filtered by selected label)
3. **Set Score Range**: Use slider to filter by AI confidence scores
4. **View Results**: See count of matching images and select for comparison

### Comparing Images

1. **Select Images**: Choose from filtered results in side-by-side dropdowns
2. **View Details**: Expand details panels to see metadata and file information
3. **Navigate**: Use different filter combinations to explore your dataset

### Example Workflow

```
1. Filter by Label: "vehicle" 
   └── Results: 1,234 images

2. Refine by Label Detail: "car"
   └── Results: 892 images  

3. Set Score Range: 0.8 - 1.0 (high confidence only)
   └── Results: 234 images

4. Compare: Select two high-confidence car predictions for side-by-side analysis
```

## 📁 Project Structure

```
lakebase_image_serving/
├── app.py                    # Main Streamlit application
├── app.yaml                  # App runtime configuration
├── databricks.yml           # Databricks Asset Bundle config
├── requirements.txt         # Python dependencies
├── resources/
│   └── db.app.yml           # Database resource configuration
└── src/
    ├── config.py            # Application configuration
    ├── database.py          # PostgreSQL database operations
    ├── image_service.py     # Unity Catalog image handling
    └── ui_components.py     # Streamlit UI components
```

## 🛠️ Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export PGDATABASE="your_db"
# ... other variables

# Run locally
streamlit run app.py
```

### Customization

- **Modify filtering**: Update `display_filtering_controls()` in `ui_components.py`
- **Add new metadata**: Extend database queries in `database.py`
- **Styling changes**: Customize Streamlit components in `ui_components.py`
- **Volume paths**: Update `VOLUME_BASE_PATH` in `config.py`

## 📊 Example Use Cases

### Computer Vision Model Evaluation
- Filter by confidence scores to review low-confidence predictions
- Compare similar labels to understand model confusion patterns
- Browse specific object categories for dataset quality assessment

### Data Exploration
- Explore AI prediction distributions across your image dataset
- Identify gaps in training data by filtering unusual combinations
- Review high-confidence predictions for model validation

### Business Intelligence
- Filter by business-relevant labels (products, people, locations)
- Compare prediction accuracy across different image categories
- Generate insights from AI-powered image analysis results

## 🤝 Contributing

This application serves as an example for building Lakebase-powered data applications. Key patterns demonstrated:

- **Real-time sync**: Using Lakebase to sync Unity Catalog data to PostgreSQL
- **OAuth integration**: Secure authentication with Databricks workspaces
- **Filtering UI**: Building responsive filter interfaces with Streamlit
- **File access**: Accessing Unity Catalog volumes from web applications

## 📄 License

This project is provided as an example for building Lakebase applications with Databricks Unity Catalog.

## 🆘 Support

For setup assistance:

1. Verify all environment variables are correctly set
2. Confirm Lakebase sync is working for your `image_predictions` table
3. Check Unity Catalog volume permissions and file accessibility
4. Review Databricks Apps resource configuration

For technical questions, refer to the Lakebase documentation or contact your Databricks solutions architect.