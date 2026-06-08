# Configuration Guide

This guide will help you configure the simple chat app for your Databricks workspace.

## Step 1: Get Your Endpoint Name

1. Go to your Databricks workspace
2. Navigate to **Serving** in the left sidebar
3. Find your Model Serving endpoint (MAS endpoint)
4. Copy the endpoint name (e.g., `my-mas-endpoint`)

## Step 2: Get Your Workspace URL

Your workspace URL looks like:
- AWS: `https://xxxxx.cloud.databricks.com`
- Azure: `https://adb-xxxxx.azuredatabricks.net`
- GCP: `https://xxxxx.gcp.databricks.com`

## Step 3: Update Configuration Files

### A. Update `app.yaml`

```yaml
command: ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]

env:
  - name: MAS_ENDPOINT_NAME
    value: "my-mas-endpoint"  # ← Replace with your endpoint name
```

### B. Update `databricks.yml`

Two places to update:

**Line 25** (endpoint resource):
```yaml
resources:
  - name: mas_endpoint
    serving_endpoint:
      name: my-mas-endpoint  # ← Replace with your endpoint name
      permission: CAN_QUERY
```

**Line 34** (workspace URL):
```yaml
workspace:
  host: https://xxxxx.cloud.databricks.com  # ← Replace with your workspace URL
```

## Step 4: Verify Your Configuration

Run this checklist:

- [ ] Endpoint name matches in both files
- [ ] Endpoint exists in your workspace
- [ ] Workspace URL is correct
- [ ] You have access to the endpoint

## Step 5: Deploy

```bash
# Authenticate
databricks auth login --host <your-workspace-url>

# Deploy
databricks bundle deploy
```

## Common Mistakes

❌ **Endpoint name with spaces**: `"my mas endpoint"` → Won't work  
✅ **Correct**: `"my-mas-endpoint"` or `"my_mas_endpoint"`

❌ **Wrong workspace URL**: `https://databricks.com`  
✅ **Correct**: `https://xxxxx.cloud.databricks.com`

❌ **Missing quotes** in YAML: `name: my-endpoint`  
✅ **Correct**: `name: "my-endpoint"`

## Troubleshooting

### "Endpoint not found"
- Double-check the endpoint name is exact (case-sensitive)
- Verify the endpoint is in "Running" state
- Check you're deploying to the correct workspace

### "Permission denied"
- Ensure your user has `CAN_QUERY` permission on the endpoint
- Check the endpoint is shared with your user or group
- Verify `user_api_scopes` includes `serving.serving-endpoints`

### "Workspace not found"
- Confirm the workspace URL format
- Try with/without trailing slash
- Ensure you're authenticated: `databricks auth token`

## Next Steps

After configuration:
1. Follow the [README.md](README.md) for deployment
2. Test locally first: `streamlit run app.py`
3. Then deploy to Databricks Apps

---

Need help? Check the [README.md](README.md) for full documentation!

