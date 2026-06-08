#!/usr/bin/env python3
"""
Simplified metric views deployment script for debugging.
This version removes advanced features to isolate core deployment issues.
"""

import yaml
import argparse
from pathlib import Path
from databricks.sdk import WorkspaceClient
import sys

# Add scripts directory to path for imports
sys.path.append("scripts")
from environment_manager import EnvironmentManager


def load_yaml_files(views_dir: Path):
    """Load all YAML files from the views directory."""
    yaml_files = (
        list(views_dir.glob("*.yml"))
        + list(views_dir.glob("*.yaml"))
        + list(views_dir.glob("*.yml.j2"))
        + list(views_dir.glob("*.yaml.j2"))
    )

    print(f"📂 Found {len(yaml_files)} YAML files in {views_dir}")

    metric_views = {}
    env_manager = EnvironmentManager()

    for yaml_file in yaml_files:
        try:
            # Handle template files (.j2)
            if yaml_file.suffix in [".j2"] or yaml_file.name.endswith(".yaml.j2"):
                # Process as template
                content = env_manager.process_metric_view_file(
                    yaml_file, "dev"
                )  # Use dev as default
                view_name = yaml_file.stem
                if view_name.endswith(".j2"):
                    view_name = view_name[:-3]  # Remove .j2 extension
            else:
                # Process as regular YAML
                with open(yaml_file, "r") as f:
                    content = yaml.safe_load(f)
                view_name = yaml_file.stem

            metric_views[view_name] = content
            print(f"✅ Loaded {view_name}")

        except Exception as e:
            print(f"❌ Failed to load {yaml_file}: {e}")
            return None

    return metric_views


def extract_columns(view_content):
    """Extract column names from dimensions and measures."""
    columns = []

    if "dimensions" in view_content:
        columns.extend(
            [dim["name"] for dim in view_content["dimensions"] if "name" in dim]
        )

    if "measures" in view_content:
        columns.extend(
            [
                measure["name"]
                for measure in view_content["measures"]
                if "name" in measure
            ]
        )

    return columns


def generate_metric_view_ddl(view_name, view_content, default_catalog, default_schema):
    """Generate DDL for a metric view with optional catalog/schema overrides."""
    columns = extract_columns(view_content)
    column_list = ", ".join(f"`{col}`" for col in columns)

    # Check for deployment overrides in YAML
    deployment_config = view_content.get("deployment", {})
    catalog = deployment_config.get("catalog", default_catalog)
    schema = deployment_config.get("schema", default_schema)

    qualified_view = f"`{catalog}`.`{schema}`.`{view_name}`"

    # Remove deployment section from YAML content for DDL (it's metadata, not part of metric definition)
    clean_content = {k: v for k, v in view_content.items() if k != "deployment"}
    yaml_content = yaml.dump(clean_content, default_flow_style=False)

    ddl = f"""CREATE OR REPLACE VIEW {qualified_view} (
  {column_list}
) WITH METRICS LANGUAGE YAML AS
$$
{yaml_content}
$$"""

    return ddl, catalog, schema


def main():
    parser = argparse.ArgumentParser(
        description="Simplified Databricks Metric Views Deployment"
    )
    parser.add_argument("--environment", default="dev", help="Target environment")
    parser.add_argument("--catalog", default="efeld_cuj", help="Catalog name")
    parser.add_argument("--schema", default="exercises", help="Schema name")
    parser.add_argument(
        "--warehouse-id", default="4b9b953939869799", help="SQL Warehouse ID"
    )
    parser.add_argument(
        "--views-dir",
        default="view_definitions",
        help="Directory containing view definitions",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate DDL without executing (for testing)",
    )

    args = parser.parse_args()

    print("🚀 === Simple Metric Views Deployment ===")
    print(f"🎯 Environment: {args.environment}")
    print(f"📊 Target: {args.catalog}.{args.schema}")
    print(f"🏭 Warehouse: {args.warehouse_id}")
    print(f"📁 Views Directory: {args.views_dir}")

    try:
        # Initialize Databricks client
        workspace_client = WorkspaceClient()

        # Load YAML files
        views_dir = Path(args.views_dir)
        if not views_dir.exists():
            print(f"❌ Directory {views_dir} does not exist")
            return False

        metric_views = load_yaml_files(views_dir)
        if not metric_views:
            print("❌ No valid metric views found")
            return False

        print(f"\n📦 === Deploying {len(metric_views)} Metric Views ===")

        success_count = 0
        for view_name, view_content in metric_views.items():
            try:
                print(f"🔄 Deploying {view_name}...")

                # Generate DDL with potential catalog/schema overrides
                ddl, target_catalog, target_schema = generate_metric_view_ddl(
                    view_name, view_content, args.catalog, args.schema
                )

                if target_catalog != args.catalog or target_schema != args.schema:
                    print(f"   📍 Target override: {target_catalog}.{target_schema}")

                if args.verbose or args.dry_run:
                    print(f"📄 Generated DDL for {view_name}:")
                    print(ddl)
                    print("-" * 50)

                if args.dry_run:
                    print(
                        f"✅ [DRY RUN] {view_name} would deploy to {target_catalog}.{target_schema}"
                    )
                    success_count += 1
                    continue

                # Execute DDL with target catalog/schema
                response = workspace_client.statement_execution.execute_statement(
                    warehouse_id=args.warehouse_id,
                    statement=ddl,
                    catalog=target_catalog,
                    schema=target_schema,
                    wait_timeout="30s",
                )

                # Wait for completion
                while response.status.state.value not in (
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELED",
                ):
                    import time

                    time.sleep(1)
                    response = workspace_client.statement_execution.get_statement(
                        response.statement_id
                    )

                if response.status.state.value == "SUCCEEDED":
                    print(f"✅ {view_name} deployed successfully")
                    success_count += 1

                    # Try to apply tags
                    try:
                        qualified_view = (
                            f"`{target_catalog}`.`{target_schema}`.`{view_name}`"
                        )
                        tag_sql = f"ALTER TABLE {qualified_view} SET TAGS ('system.Certified')"

                        tag_response = (
                            workspace_client.statement_execution.execute_statement(
                                warehouse_id=args.warehouse_id,
                                statement=tag_sql,
                                catalog=target_catalog,
                                schema=target_schema,
                                wait_timeout="30s",
                            )
                        )

                        # Wait for tag completion
                        while tag_response.status.state.value not in (
                            "SUCCEEDED",
                            "FAILED",
                            "CANCELED",
                        ):
                            import time

                            time.sleep(1)
                            tag_response = (
                                workspace_client.statement_execution.get_statement(
                                    tag_response.statement_id
                                )
                            )

                        if tag_response.status.state.value == "SUCCEEDED":
                            print(f"🏷️ {view_name} tagged successfully")
                        else:
                            print(
                                f"⚠️ Failed to tag {view_name}: {tag_response.status.error}"
                            )

                    except Exception as tag_error:
                        print(f"⚠️ Failed to tag {view_name}: {str(tag_error)}")

                else:
                    error_msg = response.status.error or "Unknown error"
                    print(f"❌ Failed to deploy {view_name}: {error_msg}")

            except Exception as e:
                print(f"❌ Error deploying {view_name}: {str(e)}")

        print("\n🎉 === Deployment Summary ===")
        print(f"✅ Successfully deployed: {success_count}/{len(metric_views)} views")

        if success_count == len(metric_views):
            print("🎊 All metric views deployed successfully!")
            return True
        else:
            print("⚠️ Some deployments failed - check logs above")
            return False

    except Exception as e:
        print(f"💥 Fatal error: {str(e)}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    if success:
        print("✨ Script completed successfully")
    else:
        print("💥 Script completed with errors")
