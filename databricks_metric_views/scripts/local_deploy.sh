#!/bin/bash

# Local deployment script for DABs metric views
# Usage: ./scripts/local_deploy.sh [dev|staging|prod]

set -e

ENVIRONMENT=${1:-dev}

echo "🚀 Deploying Metric Views using DABs with Optimized Compute to environment: $ENVIRONMENT"

# Check if databricks CLI is installed
if ! command -v databricks &> /dev/null; then
    echo "❌ Databricks CLI is not installed. Please install it first:"
    echo "   curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh"
    exit 1
fi

# Check if we're in the right directory
if [ ! -f "databricks.yml" ]; then
    echo "❌ databricks.yml not found. Please run this script from the project root directory."
    exit 1
fi

# Validate the bundle configuration
echo "🔍 Validating bundle configuration..."
databricks bundle validate --target $ENVIRONMENT

# Deploy the bundle
echo "📦 Deploying bundle to $ENVIRONMENT..."
databricks bundle deploy --target $ENVIRONMENT

# Run the metric views deployment job with optimized compute
echo "🏃 Running metric views deployment job (using optimized compute)..."
databricks bundle run --target $ENVIRONMENT metric_views_deployment

echo "✅ Deployment completed successfully!"
echo "⚡ Used optimized compute configuration for efficiency and fast startup"
echo ""
echo "📊 You can monitor your deployment at:"
echo "   https://e2-demo-field-eng.cloud.databricks.com/#job/list"
echo ""
echo "🔍 To check deployed metric views, run:"
echo "   databricks sql --warehouse-id 4b9b953939869799 --query \"SHOW VIEWS IN \\\`efeld_cuj\\\`.\\\`exercises\\\`\""
