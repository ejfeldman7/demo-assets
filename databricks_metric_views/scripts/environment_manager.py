#!/usr/bin/env python3
"""
Environment configuration management with Jinja2 templating support.
"""

import yaml
import json
from pathlib import Path
from typing import Dict, Any
from jinja2 import Environment, FileSystemLoader, TemplateError, StrictUndefined
import argparse


class EnvironmentManager:
    """Manages environment-specific configuration and templating."""

    def __init__(self, config_path: str = "config/environments.yml"):
        # Handle different working directory contexts (local vs Databricks workspace)
        self.config_path = Path(config_path)

        # If the default path doesn't exist, try alternative paths
        if not self.config_path.exists():
            # Try alternative paths without using __file__
            alt_paths = [
                Path.cwd() / config_path,
                Path("../") / config_path,  # Try one level up
            ]

            for alt_path in alt_paths:
                if alt_path.exists():
                    self.config_path = alt_path
                    break
        self._config = None
        self._jinja_env = Environment(
            loader=FileSystemLoader([".", "view_definitions"]),
            undefined=StrictUndefined,  # Fail on undefined variables
        )

    @property
    def config(self) -> Dict[str, Any]:
        """Load configuration lazily."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> Dict[str, Any]:
        """Load environment configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Environment configuration not found: {self.config_path}"
            )

        with open(self.config_path, "r") as f:
            return yaml.safe_load(f)

    def get_environment_config(self, environment: str) -> Dict[str, Any]:
        """Get configuration for a specific environment."""
        if environment not in self.config:
            available = list(self.config.keys())
            raise ValueError(
                f"Environment '{environment}' not found. Available: {available}"
            )

        # Merge global config with environment-specific config
        env_config = {}
        if "global" in self.config:
            env_config.update(self.config["global"])

        env_config.update(self.config[environment])
        return env_config

    def get_template_context(self, environment: str) -> Dict[str, Any]:
        """Get template context with both merged config and separate global namespace."""
        env_config = self.get_environment_config(environment)

        # Add separate global namespace for template access
        if "global" in self.config:
            env_config["global"] = self.config["global"]

        return env_config

    def list_environments(self) -> list[str]:
        """List all available environments."""
        return [env for env in self.config.keys() if env != "global"]

    def render_template_string(
        self, template_str: str, variables: Dict[str, Any]
    ) -> str:
        """Render a template string with variables."""
        try:
            template = self._jinja_env.from_string(template_str)
            return template.render(**variables)
        except TemplateError as e:
            raise ValueError(f"Template rendering error: {e}")

    def render_template_file(
        self, template_path: Path, variables: Dict[str, Any]
    ) -> str:
        """Render a template file with variables."""
        try:
            with open(template_path, "r") as f:
                template_str = f.read()
            return self.render_template_string(template_str, variables)
        except (FileNotFoundError, TemplateError) as e:
            raise ValueError(f"Error rendering template {template_path}: {e}")

    def process_metric_view_file(
        self, yaml_path: Path, environment: str
    ) -> Dict[str, Any]:
        """Process a metric view YAML file with environment-specific variables."""
        env_config = self.get_environment_config(environment)

        # Check if this is a template file
        if yaml_path.suffix in [".j2", ".jinja2"] or yaml_path.name.endswith(
            ".yaml.j2"
        ):
            # Use template context that includes separate global namespace
            template_context = self.get_template_context(environment)
            rendered_yaml = self.render_template_file(yaml_path, template_context)
            return yaml.safe_load(rendered_yaml)
        else:
            # Regular YAML file, load as-is but still substitute variables if they exist
            with open(yaml_path, "r") as f:
                content = f.read()

            # Simple variable substitution for non-template files
            try:
                rendered_content = self.render_template_string(content, env_config)
                return yaml.safe_load(rendered_content)
            except Exception:
                # If template rendering fails, try loading as regular YAML
                return yaml.safe_load(content)

    def validate_environment_config(self, environment: str) -> list[str]:
        """Validate environment configuration and return any issues."""
        issues = []

        try:
            config = self.get_environment_config(environment)
        except (ValueError, FileNotFoundError) as e:
            return [str(e)]

        # Check required fields
        required_fields = ["catalog", "schema", "warehouse_id"]
        for field in required_fields:
            if field not in config:
                issues.append(
                    f"Missing required field '{field}' in environment '{environment}'"
                )

        # Validate data types
        if "warehouse_id" in config and not isinstance(config["warehouse_id"], str):
            issues.append(
                f"warehouse_id must be a string in environment '{environment}'"
            )

        if "tags" in config and not isinstance(config["tags"], dict):
            issues.append(f"tags must be a dictionary in environment '{environment}'")

        return issues


def main():
    parser = argparse.ArgumentParser(description="Environment configuration management")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List environments
    subparsers.add_parser("list", help="List available environments")

    # Show environment config
    show_parser = subparsers.add_parser("show", help="Show environment configuration")
    show_parser.add_argument("environment", help="Environment name")
    show_parser.add_argument(
        "--format", choices=["yaml", "json"], default="yaml", help="Output format"
    )

    # Validate environments
    validate_parser = subparsers.add_parser(
        "validate", help="Validate environment configurations"
    )
    validate_parser.add_argument(
        "--environment", help="Specific environment to validate"
    )

    # Test template rendering
    test_parser = subparsers.add_parser("test", help="Test template rendering")
    test_parser.add_argument("template_file", help="Template file to test")
    test_parser.add_argument("environment", help="Environment to use for variables")

    args = parser.parse_args()

    manager = EnvironmentManager()

    if args.command == "list":
        envs = manager.list_environments()
        print("🌍 Available environments:")
        for env in envs:
            print(f"  • {env}")

    elif args.command == "show":
        try:
            config = manager.get_environment_config(args.environment)
            if args.format == "json":
                print(json.dumps(config, indent=2))
            else:
                print(yaml.dump(config, default_flow_style=False))
        except ValueError as e:
            print(f"❌ Error: {e}")

    elif args.command == "validate":
        if args.environment:
            environments = [args.environment]
        else:
            environments = manager.list_environments()

        all_valid = True
        for env in environments:
            issues = manager.validate_environment_config(env)
            if issues:
                all_valid = False
                print(f"❌ {env}: {len(issues)} issue(s)")
                for issue in issues:
                    print(f"   • {issue}")
            else:
                print(f"✅ {env}: Valid")

        if not all_valid:
            exit(1)

    elif args.command == "test":
        try:
            template_path = Path(args.template_file)
            if not template_path.exists():
                print(f"❌ Template file not found: {template_path}")
                exit(1)

            template_context = manager.get_template_context(args.environment)
            rendered = manager.render_template_file(template_path, template_context)

            print(
                f"✅ Template rendered successfully for environment '{args.environment}':"
            )
            print("=" * 50)
            print(rendered)

            # Try to parse as YAML to validate structure
            try:
                yaml.safe_load(rendered)
                print("\n✅ Rendered output is valid YAML")
            except yaml.YAMLError as e:
                print(f"\n⚠️  Warning: Rendered output is not valid YAML: {e}")

        except Exception as e:
            print(f"❌ Error: {e}")
            exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
