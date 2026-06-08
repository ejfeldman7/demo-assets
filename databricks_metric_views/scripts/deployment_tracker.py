#!/usr/bin/env python3
"""
Deployment status tracking and reporting for metric views.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict
import argparse


@dataclass
class DeploymentRecord:
    """Record of a single metric view deployment."""

    view_name: str
    file_path: str
    status: str  # 'success', 'failed', 'pending'
    timestamp: str
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None
    sql_generated: Optional[str] = None


@dataclass
class DeploymentSummary:
    """Summary of an entire deployment run."""

    deployment_id: str
    target_environment: str
    total_files: int
    successful_deployments: int
    failed_deployments: int
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    records: List[DeploymentRecord] = None


class DeploymentTracker:
    def __init__(self, output_dir: str = ".databricks/deployments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.current_summary: Optional[DeploymentSummary] = None

    def start_deployment(self, target_environment: str, total_files: int) -> str:
        """Start tracking a new deployment."""
        deployment_id = f"{target_environment}_{int(time.time())}"

        self.current_summary = DeploymentSummary(
            deployment_id=deployment_id,
            target_environment=target_environment,
            total_files=total_files,
            successful_deployments=0,
            failed_deployments=0,
            start_time=datetime.now(timezone.utc).isoformat(),
            records=[],
        )

        print(f"🚀 Starting deployment: {deployment_id}")
        print(f"📊 Environment: {target_environment}")
        print(f"📁 Files to process: {total_files}")

        return deployment_id

    def record_deployment(
        self,
        view_name: str,
        file_path: str,
        status: str,
        duration_seconds: float = None,
        error_message: str = None,
        sql_generated: str = None,
    ):
        """Record the result of deploying a single metric view."""
        if not self.current_summary:
            raise ValueError("No deployment in progress. Call start_deployment first.")

        record = DeploymentRecord(
            view_name=view_name,
            file_path=file_path,
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=duration_seconds,
            error_message=error_message,
            sql_generated=sql_generated,
        )

        self.current_summary.records.append(record)

        if status == "success":
            self.current_summary.successful_deployments += 1
            status_icon = "✅"
        elif status == "failed":
            self.current_summary.failed_deployments += 1
            status_icon = "❌"
        else:
            status_icon = "⏳"

        duration_str = f" ({duration_seconds:.2f}s)" if duration_seconds else ""
        print(f"{status_icon} {view_name}{duration_str}")

        if error_message:
            print(f"   💥 Error: {error_message}")

    def finish_deployment(self) -> DeploymentSummary:
        """Finish tracking the current deployment and save results."""
        if not self.current_summary:
            raise ValueError("No deployment in progress.")

        end_time = datetime.now(timezone.utc)
        self.current_summary.end_time = end_time.isoformat()

        start_time = datetime.fromisoformat(
            self.current_summary.start_time.replace("Z", "+00:00")
        )
        self.current_summary.duration_seconds = (end_time - start_time).total_seconds()

        # Save to file
        output_file = self.output_dir / f"{self.current_summary.deployment_id}.json"
        with open(output_file, "w") as f:
            json.dump(asdict(self.current_summary), f, indent=2)

        # Save as latest
        latest_file = self.output_dir / "latest.json"
        with open(latest_file, "w") as f:
            json.dump(asdict(self.current_summary), f, indent=2)

        # Print summary
        success_rate = (
            (
                self.current_summary.successful_deployments
                / self.current_summary.total_files
                * 100
            )
            if self.current_summary.total_files > 0
            else 0
        )

        print("\n🏁 === Deployment Complete ===")
        print(f"⏱️  Duration: {self.current_summary.duration_seconds:.2f} seconds")
        print(f"✅ Successful: {self.current_summary.successful_deployments}")
        print(f"❌ Failed: {self.current_summary.failed_deployments}")
        print(f"📈 Success Rate: {success_rate:.1f}%")
        print(f"📂 Report saved: {output_file}")

        summary = self.current_summary
        self.current_summary = None
        return summary

    def get_deployment_history(self, limit: int = 10) -> List[DeploymentSummary]:
        """Get recent deployment history."""
        deployment_files = sorted(
            [f for f in self.output_dir.glob("*.json") if f.name != "latest.json"],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

        summaries = []
        for file in deployment_files[:limit]:
            try:
                with open(file, "r") as f:
                    data = json.load(f)
                    # Convert records back to dataclass objects
                    if "records" in data and data["records"]:
                        data["records"] = [
                            DeploymentRecord(**record) for record in data["records"]
                        ]
                    summary = DeploymentSummary(**data)
                    summaries.append(summary)
            except Exception as e:
                print(f"⚠️ Error reading {file}: {e}")

        return summaries

    def generate_report(self, summary: DeploymentSummary) -> str:
        """Generate a human-readable deployment report."""
        success_rate = (
            (summary.successful_deployments / summary.total_files * 100)
            if summary.total_files > 0
            else 0
        )

        report = f"""
📋 === Deployment Report ===
🎯 Environment: {summary.target_environment}
🔢 Deployment ID: {summary.deployment_id}
⏰ Start Time: {summary.start_time}
⏱️  Duration: {summary.duration_seconds:.2f} seconds

📊 Results:
  📁 Total Files: {summary.total_files}
  ✅ Successful: {summary.successful_deployments}
  ❌ Failed: {summary.failed_deployments}
  📈 Success Rate: {success_rate:.1f}%

"""

        if summary.records:
            report += "📋 Individual Results:\n"
            for record in summary.records:
                status_icon = (
                    "✅"
                    if record.status == "success"
                    else "❌"
                    if record.status == "failed"
                    else "⏳"
                )
                duration_str = (
                    f" ({record.duration_seconds:.2f}s)"
                    if record.duration_seconds
                    else ""
                )
                report += f"  {status_icon} {record.view_name}{duration_str}\n"

                if record.error_message:
                    # Truncate long error messages
                    error_msg = record.error_message
                    if len(error_msg) > 100:
                        error_msg = error_msg[:100] + "..."
                    report += f"     💥 Error: {error_msg}\n"

        return report

    def get_latest_deployment(self) -> Optional[DeploymentSummary]:
        """Get the latest deployment summary."""
        latest_file = self.output_dir / "latest.json"
        if not latest_file.exists():
            return None

        try:
            with open(latest_file, "r") as f:
                data = json.load(f)
                if "records" in data and data["records"]:
                    data["records"] = [
                        DeploymentRecord(**record) for record in data["records"]
                    ]
                return DeploymentSummary(**data)
        except Exception as e:
            print(f"⚠️ Error reading latest deployment: {e}")
            return None


def main():
    parser = argparse.ArgumentParser(description="Deployment tracking utilities")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # History command
    history_parser = subparsers.add_parser("history", help="Show deployment history")
    history_parser.add_argument(
        "--limit", type=int, default=10, help="Number of deployments to show"
    )
    history_parser.add_argument("--environment", help="Filter by environment")

    # Report command
    report_parser = subparsers.add_parser(
        "report", help="Generate report for latest deployment"
    )
    report_parser.add_argument(
        "--deployment-id", help="Specific deployment ID to report on"
    )

    # Status command
    subparsers.add_parser("status", help="Show current deployment status")

    args = parser.parse_args()
    tracker = DeploymentTracker()

    if args.command == "history":
        summaries = tracker.get_deployment_history(args.limit)

        if args.environment:
            summaries = [
                s for s in summaries if s.target_environment == args.environment
            ]

        if not summaries:
            print("📭 No deployment history found")
            return

        print(f"\n📚 === Recent Deployments (last {len(summaries)}) ===")
        print(
            f"{'Timestamp':<20} {'Environment':<12} {'Status':<25} {'Success Rate':<12}"
        )
        print("-" * 70)

        for summary in summaries:
            success_rate = (
                (summary.successful_deployments / summary.total_files * 100)
                if summary.total_files > 0
                else 0
            )
            status = (
                "✅ Success"
                if summary.failed_deployments == 0
                else f"❌ {summary.failed_deployments} failed"
            )
            timestamp = summary.start_time[:19].replace("T", " ")
            print(
                f"{timestamp:<20} {summary.target_environment:<12} {status:<25} {success_rate:>8.1f}%"
            )

    elif args.command == "report":
        if args.deployment_id:
            # Load specific deployment
            file_path = tracker.output_dir / f"{args.deployment_id}.json"
            if not file_path.exists():
                print(f"❌ Deployment {args.deployment_id} not found")
                return

            with open(file_path, "r") as f:
                data = json.load(f)
                if "records" in data and data["records"]:
                    data["records"] = [
                        DeploymentRecord(**record) for record in data["records"]
                    ]
                summary = DeploymentSummary(**data)
        else:
            # Load latest deployment
            summary = tracker.get_latest_deployment()
            if not summary:
                print("❌ No deployment records found")
                return

        print(tracker.generate_report(summary))

    elif args.command == "status":
        summary = tracker.get_latest_deployment()
        if not summary:
            print("📭 No deployment records found")
            return

        print("\n📊 === Latest Deployment Status ===")
        print(f"🎯 Environment: {summary.target_environment}")
        print(f"🔢 Deployment ID: {summary.deployment_id}")
        print(f"⏰ Start Time: {summary.start_time}")

        if summary.end_time:
            success_rate = (
                (summary.successful_deployments / summary.total_files * 100)
                if summary.total_files > 0
                else 0
            )
            print("🏁 Status: Complete")
            print(
                f"✅ Successful: {summary.successful_deployments}/{summary.total_files}"
            )
            print(f"❌ Failed: {summary.failed_deployments}/{summary.total_files}")
            print(f"📈 Success Rate: {success_rate:.1f}%")
        else:
            print("🔄 Status: In Progress...")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
