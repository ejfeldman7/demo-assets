# Deployment Guide

This guide walks you through deploying the Chunking Playground to your Databricks workspace.

## Prerequisites

Before deploying, ensure you have:

- ✅ Databricks workspace with Unity Catalog enabled
- ✅ Databricks CLI installed and configured
- ✅ Admin or catalog owner permissions (to grant access to the app)
- ✅ Unity Catalog volume(s) with documents to test (PDF, TXT, or MD files)
- ✅ (Optional) Access to a Databricks Model Serving embedding endpoint

## Step 1: Configure Databricks CLI

If you haven't configured the Databricks CLI yet:

```bash
databricks auth login --host https://your-workspace.cloud.databricks.com
```

This will open a browser window to authenticate. Follow the prompts to complete authentication.

## Step 2: Deploy the App

Navigate to the chunking_playground directory and deploy:

```bash
cd chunking_playground
databricks apps deploy chunking_playground
```

The deployment process will:
1. Create a service principal for the app
2. Package and upload the source code
3. Start the app on Databricks Apps (serverless)

**Important**: Note the service principal name from the deployment output. It will look like:
```
Service Principal: app-xxxxx chunking-playground
```

You'll need this in the next step.

## Step 3: Grant Permissions

The app needs permissions to read files from your Unity Catalog volumes. Grant these permissions using SQL.

### Option A: Using SQL Editor (Recommended)

1. Open the **SQL Editor** in your Databricks workspace
2. Select a SQL warehouse
3. Run the following SQL commands (replace placeholders with your values):

```sql
-- Grant catalog access
GRANT USE CATALOG ON CATALOG `your_catalog_name` 
TO `app-xxxxx chunking-playground`;

-- Grant schema access
GRANT USE SCHEMA ON SCHEMA `your_catalog_name`.`your_schema_name` 
TO `app-xxxxx chunking-playground`;

-- Grant volume read access
GRANT READ VOLUME ON VOLUME `your_catalog_name`.`your_schema_name`.`your_volume_name` 
TO `app-xxxxx chunking-playground`;
```

### Option B: Using a Notebook

Create a notebook with the following cells:

```sql
-- Cell 1: Grant catalog access
GRANT USE CATALOG ON CATALOG `your_catalog_name` 
TO `app-xxxxx chunking-playground`;
```

```sql
-- Cell 2: Grant schema access
GRANT USE SCHEMA ON SCHEMA `your_catalog_name`.`your_schema_name` 
TO `app-xxxxx chunking-playground`;
```

```sql
-- Cell 3: Grant volume read access
GRANT READ VOLUME ON VOLUME `your_catalog_name`.`your_schema_name`.`your_volume_name` 
TO `app-xxxxx chunking-playground`;
```

Run all cells to grant permissions.

### Verify Permissions

To verify the permissions were granted correctly:

```sql
-- Check catalog permissions
SHOW GRANTS ON CATALOG `your_catalog_name`;

-- Check schema permissions
SHOW GRANTS ON SCHEMA `your_catalog_name`.`your_schema_name`;

-- Check volume permissions
SHOW GRANTS ON VOLUME `your_catalog_name`.`your_schema_name`.`your_volume_name`;
```

## Step 4: Access the App

1. Navigate to your Databricks workspace
2. Go to **Apps** in the left sidebar
3. Find **chunking-playground** in the list
4. Click to open the app

The app should now be accessible and able to read files from the volumes you granted access to.

## Step 5: Test the App

1. In the app sidebar, navigate through:
   - **Catalog** → Select your catalog
   - **Schema** → Select your schema
   - **Volume** → Select your volume
2. You should see a list of files (PDF, TXT, MD)
3. Select a file and click **"Load File"**
4. Configure chunking strategies and test!

## Granting Access to Additional Volumes

To grant access to more volumes after initial deployment:

```sql
-- For each additional volume
GRANT USE CATALOG ON CATALOG `catalog_name` 
TO `app-xxxxx chunking-playground`;

GRANT USE SCHEMA ON SCHEMA `catalog_name`.`schema_name` 
TO `app-xxxxx chunking-playground`;

GRANT READ VOLUME ON VOLUME `catalog_name`.`schema_name`.`volume_name` 
TO `app-xxxxx chunking-playground`;
```

## Updating the App

To update the app after making code changes:

```bash
databricks apps deploy chunking_playground
```

This will redeploy the app with your latest changes. Permissions are preserved across updates.

## Troubleshooting

### "No catalogs found"
- **Cause**: Service principal doesn't have USE CATALOG permissions
- **Solution**: Run the GRANT USE CATALOG command from Step 3

### "No volumes found" 
- **Cause**: Service principal doesn't have USE SCHEMA permissions
- **Solution**: Run the GRANT USE SCHEMA command from Step 3

### "Failed to download file"
- **Cause**: Service principal doesn't have READ VOLUME permissions
- **Solution**: Run the GRANT READ VOLUME command from Step 3

### "Permission denied" errors in SQL
- **Cause**: You don't have admin or owner permissions on the catalog/schema/volume
- **Solution**: Ask a workspace admin or catalog owner to run the GRANT commands

### App deployment fails
- **Cause**: Databricks CLI not configured correctly
- **Solution**: Run `databricks auth login` again and verify your workspace URL

### Can't find the app after deployment
- **Cause**: App may still be starting up
- **Solution**: Wait 1-2 minutes and refresh the Apps page

## Uninstalling the App

To remove the app from your workspace:

```bash
databricks apps delete chunking_playground
```

This will:
- Stop the app
- Delete the app resources
- Remove the service principal

Note: This does NOT revoke the permissions. If you redeploy later with the same name, you may need to re-grant permissions.

## Security Considerations

- The app runs with a dedicated service principal (not your personal credentials)
- Only grant READ VOLUME permissions (never WRITE)
- The app processes files in ephemeral storage (no persistent data)
- All authentication is handled by Databricks workspace
- Files are downloaded temporarily and cleaned up after processing

## Additional Resources

- [Databricks Apps Documentation](https://docs.databricks.com/en/dev-tools/databricks-apps/)
- [Unity Catalog Permissions](https://docs.databricks.com/en/data-governance/unity-catalog/manage-privileges/)
- [Databricks CLI Setup](https://docs.databricks.com/en/dev-tools/cli/)

## Support

For issues specific to:
- **Databricks platform**: Contact Databricks support
- **Unity Catalog permissions**: Consult your workspace admin
- **App functionality**: Review the [README.md](README.md) and [QUICKSTART.md](QUICKSTART.md)

---

**Ready to deploy? Start with Step 1! 🚀**

