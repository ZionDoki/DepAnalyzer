"""Main CLI entry point for depanalyzer.

Provides commands: scan, query, explain, export
"""

import argparse
import logging
import sys
from pathlib import Path

from depanalyzer.cli.scan import scan_command
from depanalyzer.cli.export import export_command

logger = logging.getLogger("depanalyzer.cli")


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration.

    Args:
        verbose: Enable verbose logging.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    """Main CLI entry point.

    Returns:
        int: Exit code.
    """
    parser = argparse.ArgumentParser(
        description="Depanalyzer - Dependency Analysis Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Scan command
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan repository and build dependency graph",
    )
    scan_parser.add_argument(
        "source",
        help="Local path or Git URL to analyze",
    )
    scan_parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output graph file (.json, .gml, or .dot)",
    )
    scan_parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=8,
        help="Maximum concurrent workers (default: 8)",
    )
    scan_parser.add_argument(
        "-d",
        "--max-depth",
        type=int,
        default=3,
        help="Maximum third-party dependency depth (default: 3)",
    )
    scan_parser.add_argument(
        "--max-deps",
        type=int,
        help="Global third-party dependency limit",
    )
    scan_parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="Skip analysis phase",
    )

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export existing graph to different format",
    )
    export_parser.add_argument(
        "graph_id",
        help="Graph ID to export (from scan output)",
    )
    export_parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output file",
    )
    export_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "gml", "dot", "asset_artifact"],
        default="json",
        help="Output format (default: json, use asset_artifact for asset->artifact mapping)",
    )
    export_parser.add_argument(
        "--with-deps",
        action="store_true",
        help="Include dependency graphs in export",
    )

    # Query command (placeholder)
    query_parser = subparsers.add_parser(
        "query",
        help="Query existing graph (not yet implemented)",
    )
    query_parser.add_argument(
        "graph",
        help="Graph file to query",
    )

    # Explain command (placeholder)
    explain_parser = subparsers.add_parser(
        "explain",
        help="Explain dependency chains (not yet implemented)",
    )
    explain_parser.add_argument(
        "graph",
        help="Graph file to analyze",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Dispatch to subcommand
    if args.command == "scan":
        return scan_command(args)
    elif args.command == "export":
        return export_command(args)
    elif args.command == "query":
        logger.error("Query command not yet implemented")
        return 1
    elif args.command == "explain":
        logger.error("Explain command not yet implemented")
        return 1
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
