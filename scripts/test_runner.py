#!/usr/bin/env python3
"""
Automated testing framework for deployed metric views.
This script runs validation tests against deployed metric views to ensure correctness.
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from databricks.sdk import WorkspaceClient
import databricks.sql as sql
from databricks.sdk.core import Config
import time
import traceback

# Import our environment manager
sys.path.append("scripts")
from environment_manager import EnvironmentManager


class TestException(Exception):
    """Custom exception for test failures that should fail the Databricks task."""

    pass


@dataclass
class TestCondition:
    """Represents a test condition/assertion."""

    column: str
    operator: str
    value: Any
    error_message: str


@dataclass
class TestDefinition:
    """Represents a single test definition."""

    test_name: str
    description: str
    query_index: int
    expected_conditions: List[TestCondition]


@dataclass
class TestResult:
    """Represents the result of a test execution."""

    test_name: str
    passed: bool
    error_message: Optional[str] = None
    actual_values: Optional[Dict[str, Any]] = None
    execution_time: Optional[float] = None


class MetricViewTester:
    """Runs automated tests against deployed metric views."""

    def __init__(
        self, warehouse_id: str, catalog: str, schema: str, profile: str = None
    ):
        self.warehouse_id = warehouse_id
        self.catalog = catalog
        self.schema = schema
        self.profile = profile

        # Initialize clients
        self.workspace_client = (
            WorkspaceClient(profile=profile) if profile else WorkspaceClient()
        )
        self.env_manager = EnvironmentManager()

    def execute_sql_query(self, sql_query: str) -> List[Dict[str, Any]]:
        """Execute SQL query using databricks-sql-connector and return results."""
        try:
            print(f"🔍 Debug: Executing SQL query: {sql_query[:100]}...")
            print(f"🔍 Debug: Connecting to warehouse: {self.warehouse_id}")

            # Use the recommended pattern from Databricks Apps Cookbook
            cfg = Config()

            with sql.connect(
                server_hostname=cfg.host,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: cfg.authenticate,
            ) as connection:
                print("✅ Debug: Connection established successfully")

                with connection.cursor() as cursor:
                    print("🔍 Debug: Executing query...")
                    # Use fully qualified table names in the query instead of setting catalog/schema in connection
                    cursor.execute(sql_query)

                    print("🔍 Debug: Fetching results...")
                    # Use fetchall_arrow().to_pandas() to get proper column names
                    df = cursor.fetchall_arrow().to_pandas()

                    print(
                        f"✅ Debug: Query completed, got {len(df)} rows with columns: {list(df.columns)}"
                    )

                    # Convert DataFrame to list of dictionaries
                    return df.to_dict("records")

        except Exception as e:
            error_msg = f"SQL execution failed: {str(e)}"
            print(f"❌ Debug: {error_msg}")
            raise TestException(error_msg)

    def load_test_queries(self, test_file: Path, environment: str) -> List[str]:
        """Load and render test queries from SQL file."""
        # Handle different working directory contexts (local vs Databricks workspace)
        if not test_file.exists():
            # Try alternative paths without using __file__
            alt_paths = [
                Path.cwd() / test_file,
                Path("../") / test_file,  # Try one level up
            ]

            found = False
            for alt_path in alt_paths:
                if alt_path.exists():
                    test_file = alt_path
                    found = True
                    break

            if not found:
                raise FileNotFoundError(f"Test file not found: {test_file}")

        with open(test_file, "r") as f:
            content = f.read()

        # Get environment configuration for templating
        env_config = self.env_manager.get_environment_config(environment)

        # Render template with environment variables
        try:
            rendered_content = self.env_manager.render_template_string(
                content, env_config
            )
        except Exception as e:
            print(f"❌ Template rendering failed: {e}")
            # Fallback to non-rendered content
            rendered_content = content

        # Split queries by semicolon and extract SQL statements
        raw_queries = rendered_content.split(";")

        queries = []
        for query_section in raw_queries:
            query_section = query_section.strip()

            if not query_section:
                continue

            # Extract SQL from the section by removing comment lines
            lines = query_section.split("\n")
            sql_lines = []

            for line in lines:
                line = line.strip()
                # Skip empty lines and pure comment lines
                if line and not line.startswith("--"):
                    sql_lines.append(line)

            if sql_lines:
                sql_query = " ".join(sql_lines).strip()
                queries.append(sql_query)
        return queries

    def load_expected_results(self, expected_file: Path) -> List[TestDefinition]:
        """Load expected results from JSON file."""
        # Handle different working directory contexts (local vs Databricks workspace)
        if not expected_file.exists():
            # Try alternative paths without using __file__
            alt_paths = [
                Path.cwd() / expected_file,
                Path("../") / expected_file,  # Try one level up
            ]

            found = False
            for alt_path in alt_paths:
                if alt_path.exists():
                    expected_file = alt_path
                    found = True
                    break

            if not found:
                raise FileNotFoundError(
                    f"Expected results file not found: {expected_file}"
                )

        with open(expected_file, "r") as f:
            data = json.load(f)

        test_definitions = []
        for test_spec in data.get("expected_results", []):
            conditions = []
            for cond in test_spec.get("expected_conditions", []):
                conditions.append(
                    TestCondition(
                        column=cond["column"],
                        operator=cond["operator"],
                        value=cond["value"],
                        error_message=cond["error_message"],
                    )
                )

            test_definitions.append(
                TestDefinition(
                    test_name=test_spec["test_name"],
                    description=test_spec["description"],
                    query_index=test_spec["query_index"],
                    expected_conditions=conditions,
                )
            )

        return test_definitions

    def evaluate_condition(
        self, condition: TestCondition, actual_values: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Evaluate a test condition against actual results."""
        if condition.column not in actual_values:
            return False, f"Column '{condition.column}' not found in results"

        actual_value = actual_values[condition.column]
        expected_value = condition.value
        operator = condition.operator

        try:
            if operator == "=":
                passed = actual_value == expected_value
            elif operator == "!=":
                passed = actual_value != expected_value
            elif operator == ">":
                passed = actual_value > expected_value
            elif operator == ">=":
                passed = actual_value >= expected_value
            elif operator == "<":
                passed = actual_value < expected_value
            elif operator == "<=":
                passed = actual_value <= expected_value
            elif operator == "in":
                passed = actual_value in expected_value
            elif operator == "not_in":
                passed = actual_value not in expected_value
            else:
                return False, f"Unknown operator: {operator}"

            if not passed:
                error_msg = f"{condition.error_message}. Expected {condition.column} {operator} {expected_value}, got {actual_value}"
                return False, error_msg

            return True, ""

        except Exception as e:
            return False, f"Error evaluating condition: {str(e)}"

    def run_test(self, test_def: TestDefinition, queries: List[str]) -> TestResult:
        """Run a single test."""
        start_time = time.time()

        try:
            # Get the query for this test
            if test_def.query_index >= len(queries):
                return TestResult(
                    test_name=test_def.test_name,
                    passed=False,
                    error_message=f"Query index {test_def.query_index} out of range (have {len(queries)} queries)",
                )

            query = queries[test_def.query_index]

            # Execute the query
            results = self.execute_sql_query(query)
            execution_time = time.time() - start_time

            if not results:
                return TestResult(
                    test_name=test_def.test_name,
                    passed=False,
                    error_message="Query returned no results",
                    execution_time=execution_time,
                )

            # Use the first row for evaluation (most test queries return single row)
            actual_values = results[0]

            # Evaluate all conditions
            all_passed = True
            error_messages = []

            for condition in test_def.expected_conditions:
                passed, error_msg = self.evaluate_condition(condition, actual_values)
                if not passed:
                    all_passed = False
                    error_messages.append(error_msg)

            return TestResult(
                test_name=test_def.test_name,
                passed=all_passed,
                error_message="; ".join(error_messages) if error_messages else None,
                actual_values=actual_values,
                execution_time=execution_time,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            return TestResult(
                test_name=test_def.test_name,
                passed=False,
                error_message=f"Test execution failed: {str(e)}",
                execution_time=execution_time,
            )

    def run_tests_for_view(self, view_name: str, environment: str) -> List[TestResult]:
        """Run all tests for a specific view."""
        print(f"🧪 Testing view: {view_name}")

        # Find test files
        test_sql_file = Path(f"tests/test_{view_name}.sql")
        expected_results_file = Path(f"tests/expected_results/test_{view_name}.json")

        try:
            # Load test queries and expected results
            queries = self.load_test_queries(test_sql_file, environment)
            test_definitions = self.load_expected_results(expected_results_file)

            print(
                f"   📋 Found {len(test_definitions)} tests with {len(queries)} queries"
            )

            # Run each test
            results = []
            for test_def in test_definitions:
                print(f"   🔍 Running test: {test_def.test_name}")
                result = self.run_test(test_def, queries)
                results.append(result)

                status = "✅ PASS" if result.passed else "❌ FAIL"
                time_str = (
                    f"({result.execution_time:.2f}s)" if result.execution_time else ""
                )
                print(f"      {status} {time_str}")

                if not result.passed and result.error_message:
                    print(f"      💥 {result.error_message}")

            return results

        except Exception as e:
            print(f"   ❌ Error setting up tests for {view_name}: {str(e)}")
            return [
                TestResult(test_name="setup_error", passed=False, error_message=str(e))
            ]

    def run_all_tests(
        self, environment: str, view_names: Optional[List[str]] = None
    ) -> Dict[str, List[TestResult]]:
        """Run tests for all views or specific views."""
        print("🧪 === Running Metric View Tests ===")
        print(f"🎯 Environment: {environment}")
        print(f"📊 Target: {self.catalog}.{self.schema}")

        # Discover test files if no specific views provided
        if not view_names:
            # Try multiple possible paths for test files
            test_paths = [
                Path("tests"),  # Relative to current working directory
                Path.cwd() / "tests",  # Explicit relative to cwd
            ]

            # Add more paths if we can determine script location without __file__
            try:
                import inspect

                frame = inspect.currentframe()
                if frame and frame.f_back:
                    # Try to get the script directory from the call stack
                    current_file = frame.f_code.co_filename
                    if current_file and current_file != "<stdin>":
                        script_dir = Path(current_file).parent.parent
                        test_paths.append(script_dir / "tests")
            except Exception:
                pass

            view_names = []
            for test_path in test_paths:
                if test_path.exists():
                    print(f"🔍 Searching for tests in: {test_path}")
                    test_files = list(test_path.glob("test_*.sql"))
                    print(
                        f"📋 Found {len(test_files)} test files: {[f.name for f in test_files]}"
                    )

                    for test_file in test_files:
                        # Extract view name from test_<view_name>.sql
                        view_name = test_file.stem[5:]  # Remove 'test_' prefix
                        view_names.append(view_name)

                    if test_files:
                        break  # Use the first path that contains test files
                else:
                    print(f"❌ Test path does not exist: {test_path}")

            if not view_names:
                print(f"🔍 Debug: Current working directory: {Path.cwd()}")
                print(
                    f"🔍 Debug: Contents of current directory: {list(Path.cwd().iterdir())}"
                )
                # List contents of common directories
                for debug_path in [Path("."), Path("scripts"), Path("tests")]:
                    if debug_path.exists():
                        print(
                            f"🔍 Debug: Contents of {debug_path}: {list(debug_path.iterdir())}"
                        )

        if not view_names:
            print("📭 No test files found")
            return {}

        print(f"📋 Testing {len(view_names)} views: {', '.join(view_names)}")

        # Run tests for each view
        all_results = {}
        for view_name in view_names:
            all_results[view_name] = self.run_tests_for_view(view_name, environment)

        # Summary
        total_tests = sum(len(results) for results in all_results.values())
        passed_tests = sum(
            sum(1 for result in results if result.passed)
            for results in all_results.values()
        )
        failed_tests = total_tests - passed_tests

        print("\n📊 === Test Summary ===")
        print(f"✅ Passed: {passed_tests}/{total_tests}")
        print(f"❌ Failed: {failed_tests}/{total_tests}")
        print(
            f"📈 Success Rate: {(passed_tests / total_tests * 100):.1f}%"
            if total_tests > 0
            else "N/A"
        )

        return all_results


def main():
    parser = argparse.ArgumentParser(description="Run automated tests for metric views")
    parser.add_argument("--environment", required=True, help="Target environment")
    parser.add_argument("--catalog", help="Override catalog from environment config")
    parser.add_argument("--schema", help="Override schema from environment config")
    parser.add_argument(
        "--warehouse-id", help="Override warehouse ID from environment config"
    )
    parser.add_argument("--profile", help="Databricks CLI profile")
    parser.add_argument(
        "--views", nargs="+", help="Specific views to test (default: all)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    try:
        # Load environment configuration
        env_manager = EnvironmentManager()
        env_config = env_manager.get_environment_config(args.environment)

        # Use CLI arguments or fall back to environment config
        catalog = args.catalog or env_config["catalog"]
        schema = args.schema or env_config["schema"]
        warehouse_id = args.warehouse_id or env_config["warehouse_id"]

        # Initialize tester
        tester = MetricViewTester(
            warehouse_id=warehouse_id,
            catalog=catalog,
            schema=schema,
            profile=args.profile,
        )

        # Run tests
        results = tester.run_all_tests(args.environment, args.views)

        # Check if all tests passed
        all_passed = all(
            all(result.passed for result in view_results)
            for view_results in results.values()
        )

        return all_passed

    except Exception as e:
        print(f"💥 Fatal error: {str(e)}")
        if args.verbose:
            traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    if success:
        print("✨ All tests completed successfully")
    else:
        print("💥 Some tests failed")
