#!/usr/bin/env python3
"""
Enhanced YAML validation for metric views with semantic checking.
This script validates both structure and SQL expressions in metric view definitions.
"""

import yaml
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
import argparse
from dataclasses import dataclass


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    file_path: Optional[str] = None


class MetricViewValidator:
    def __init__(self):
        self.required_fields = {
            "version": str,
            "source": str,
            "dimensions": list,
            "measures": list,
        }
        self.optional_fields = {"joins": list, "filter": str}

    def validate_yaml_structure(self, yaml_content: Dict[str, Any]) -> ValidationResult:
        """Validate basic YAML structure and required fields."""
        errors = []
        warnings = []

        # Check required fields
        for field, expected_type in self.required_fields.items():
            if field not in yaml_content:
                errors.append(f"Missing required field: {field}")
            elif not isinstance(yaml_content[field], expected_type):
                errors.append(
                    f"Field '{field}' must be of type {expected_type.__name__}"
                )

        # Validate dimensions structure
        if "dimensions" in yaml_content:
            for i, dim in enumerate(yaml_content["dimensions"]):
                if not isinstance(dim, dict):
                    errors.append(f"Dimension {i} must be a dictionary")
                    continue
                if "name" not in dim:
                    errors.append(f"Dimension {i} missing required 'name' field")
                if "expr" not in dim:
                    errors.append(f"Dimension {i} missing required 'expr' field")

        # Validate measures structure
        if "measures" in yaml_content:
            for i, measure in enumerate(yaml_content["measures"]):
                if not isinstance(measure, dict):
                    errors.append(f"Measure {i} must be a dictionary")
                    continue
                if "name" not in measure:
                    errors.append(f"Measure {i} missing required 'name' field")
                if "expr" not in measure:
                    errors.append(f"Measure {i} missing required 'expr' field")

        return ValidationResult(len(errors) == 0, errors, warnings)

    def validate_sql_expressions(
        self, yaml_content: Dict[str, Any]
    ) -> ValidationResult:
        """Validate SQL expressions in dimensions and measures."""
        errors = []
        warnings = []

        # Basic SQL expression validation (can be enhanced with actual SQL parsing)
        dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER"]

        expressions_to_check = []

        # Collect all expressions
        if "dimensions" in yaml_content:
            for dim in yaml_content["dimensions"]:
                if "expr" in dim:
                    expressions_to_check.append(("dimension", dim["name"], dim["expr"]))

        if "measures" in yaml_content:
            for measure in yaml_content["measures"]:
                if "expr" in measure:
                    expressions_to_check.append(
                        ("measure", measure["name"], measure["expr"])
                    )

        if "filter" in yaml_content:
            expressions_to_check.append(
                ("filter", "global_filter", yaml_content["filter"])
            )

        # Validate expressions
        for expr_type, name, expression in expressions_to_check:
            # Check for dangerous keywords
            upper_expr = expression.upper()
            for keyword in dangerous_keywords:
                if keyword in upper_expr:
                    errors.append(
                        f"Dangerous keyword '{keyword}' found in {expr_type} '{name}'"
                    )

            # Check for balanced parentheses
            if expression.count("(") != expression.count(")"):
                errors.append(
                    f"Unbalanced parentheses in {expr_type} '{name}': {expression}"
                )

            # Check for basic SQL function syntax
            if expr_type == "measure":
                agg_functions = ["SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"]
                has_agg = any(func in upper_expr for func in agg_functions)
                if not has_agg:
                    warnings.append(
                        f"Measure '{name}' may be missing aggregation function"
                    )

        return ValidationResult(len(errors) == 0, errors, warnings)

    def validate_references(self, yaml_content: Dict[str, Any]) -> ValidationResult:
        """Validate that dimension/measure references are valid."""
        errors = []
        warnings = []

        dimension_names = set()
        if "dimensions" in yaml_content:
            dimension_names = {
                dim["name"] for dim in yaml_content["dimensions"] if "name" in dim
            }

        measure_names = set()
        if "measures" in yaml_content:
            measure_names = {
                measure["name"]
                for measure in yaml_content["measures"]
                if "name" in measure
            }

        # Check for name collisions
        name_collisions = dimension_names & measure_names
        if name_collisions:
            errors.append(
                f"Name collisions between dimensions and measures: {name_collisions}"
            )

        # Check for SQL injection patterns (basic)
        all_expressions = []
        if "dimensions" in yaml_content:
            all_expressions.extend(
                [dim.get("expr", "") for dim in yaml_content["dimensions"]]
            )
        if "measures" in yaml_content:
            all_expressions.extend(
                [measure.get("expr", "") for measure in yaml_content["measures"]]
            )

        dangerous_patterns = [
            r"--",  # SQL comments
            r"/\*.*\*/",  # Multi-line comments
            r";.*?;",  # Multiple statements
        ]

        for expr in all_expressions:
            for pattern in dangerous_patterns:
                if re.search(pattern, expr, re.IGNORECASE | re.DOTALL):
                    warnings.append(
                        f"Potentially unsafe pattern found in expression: {expr[:50]}..."
                    )

        return ValidationResult(len(errors) == 0, errors, warnings)

    def validate_file(self, file_path: Path) -> ValidationResult:
        """Validate a single YAML file."""

        # Skip Jinja2 template files - they should be validated after rendering
        if file_path.suffix == ".j2":
            return ValidationResult(
                True,
                [],
                [],
                str(file_path),
            )

        try:
            with open(file_path, "r") as f:
                yaml_content = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return ValidationResult(
                False, [f"YAML parsing error: {e}"], [], str(file_path)
            )
        except Exception as e:
            return ValidationResult(
                False, [f"File reading error: {e}"], [], str(file_path)
            )

        if yaml_content is None:
            return ValidationResult(False, ["Empty YAML file"], [], str(file_path))

        # Run all validation checks
        structure_result = self.validate_yaml_structure(yaml_content)
        sql_result = self.validate_sql_expressions(yaml_content)
        reference_result = self.validate_references(yaml_content)

        # Combine results
        all_errors = (
            structure_result.errors + sql_result.errors + reference_result.errors
        )
        all_warnings = (
            structure_result.warnings + sql_result.warnings + reference_result.warnings
        )

        return ValidationResult(
            len(all_errors) == 0, all_errors, all_warnings, str(file_path)
        )


def main():
    parser = argparse.ArgumentParser(
        description="Enhanced validation for metric view YAML files"
    )
    parser.add_argument(
        "yaml_dir",
        nargs="?",
        default="view_definitions",
        help="Directory containing YAML files (default: view_definitions)",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )

    args = parser.parse_args()

    validator = MetricViewValidator()
    yaml_dir = Path(args.yaml_dir)

    if not yaml_dir.exists():
        print(f"❌ Directory {yaml_dir} does not exist")
        sys.exit(1)

    yaml_files = (
        list(yaml_dir.glob("*.yml"))
        + list(yaml_dir.glob("*.yaml"))
        + list(yaml_dir.glob("*.j2"))
    )

    if not yaml_files:
        print(f"⚠️ No YAML files found in {yaml_dir}")
        sys.exit(0)

    all_results = []
    has_errors = False

    for yaml_file in yaml_files:
        result = validator.validate_file(yaml_file)
        all_results.append(result)

        if not result.is_valid:
            has_errors = True
        elif args.strict and result.warnings:
            has_errors = True

    # Output results
    if args.format == "json":
        import json

        output = {
            "files_validated": len(yaml_files),
            "has_errors": has_errors,
            "results": [
                {
                    "file": r.file_path,
                    "valid": r.is_valid,
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in all_results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n🔍 === Metric View Validation Results ===")
        print(f"📄 Files validated: {len(yaml_files)}")

        for result in all_results:
            status = "✅ VALID" if result.is_valid else "❌ INVALID"
            if args.strict and result.warnings:
                status = "❌ INVALID (warnings as errors)"

            print(f"\n📋 {Path(result.file_path).name}: {status}")

            if result.errors:
                print("  🚨 Errors:")
                for error in result.errors:
                    print(f"    • {error}")

            if result.warnings:
                warning_label = (
                    "  🚨 Errors (strict mode):" if args.strict else "  ⚠️  Warnings:"
                )
                print(warning_label)
                for warning in result.warnings:
                    print(f"    • {warning}")

    if has_errors:
        print("\n❌ Validation failed with errors")
        sys.exit(1)
    else:
        print("\n✅ All validations passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
