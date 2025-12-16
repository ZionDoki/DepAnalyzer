"""Main CLI entry point for depanalyzer.

Provides commands: scan, query, explain, export
"""

import argparse
import logging
import sys
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

logger = logging.getLogger("depanalyzer.cli")

# Trigger ecosystem registration by importing parsers package
import depanalyzer.parsers

from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.cli.scan import scan_command
from depanalyzer.cli.export import export_command
from depanalyzer.cli.scancode import scancode_command
from depanalyzer.cli.dag import dag_command
from depanalyzer.utils.scancode_installer import install_scancode


def setup_logging(verbose: bool = False, console: Optional[Console] = None) -> None:
    """Setup logging configuration with Rich integration.

    Args:
        verbose: Enable verbose logging.
        console: Rich Console instance for coordinated output (optional).
    """
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    # Create RichHandler for coordinated output with progress display
    handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
        tracebacks_show_locals=verbose,
        log_time_format="[%H:%M:%S]",
    )

    # Configure root logger
    logging.basicConfig(
        level=level,
        format="[%(name)s] [%(levelname)s] %(message)s",
        handlers=[handler],
    )


def main() -> int:
    """Main CLI entry point.

    Returns:
        int: Exit code.
    """
    # Verify ecosystem registration (only in main process)
    _registry = EcosystemRegistry.get_instance()
    _ecosystems = _registry.list_ecosystems()
    if not _ecosystems:
        logger.warning("⚠ WARNING: No ecosystems were registered during startup!")
        logger.warning(
            "Please check for import errors above. Scan command will not work."
        )
    else:
        logger.info(
            "✓ Successfully loaded %d ecosystem(s): %s",
            len(_ecosystems),
            ", ".join(_ecosystems),
        )

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
    parser.add_argument(
        "--install",
        nargs="?",
        const="",
        default=False,
        help=(
            "Download and install ScanCode Toolkit to a local fixed path "
            "(~/.depanalyzer/scancode-toolkit). Optional version override, e.g. "
            "--install 32.4.1 (defaults to 32.4.1)."
        ),
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
        help="Output graph file (JSON, typically .json)",
    )
    scan_parser.add_argument(
        "--cache-dir",
        help=(
            "Base directory for scan cache. Graphs and sources for the scanned "
            "project will be stored under <cache-dir>/<source_stem>/{graphs,sources}. "
            "Defaults to .dep_cache in the current working directory."
        ),
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
        help="Maximum third-party dependency depth (default: 3, only when third-party dependency scanning is enabled)",
    )
    scan_parser.add_argument(
        "--max-deps",
        type=int,
        help="Global third-party dependency limit (only when third-party dependency scanning is enabled)",
    )
    scan_parser.add_argument(
        "--concurrent-deps",
        type=int,
        default=4,
        help="Maximum concurrent dependency transactions (default: 4)",
    )
    scan_parser.add_argument(
        "-t",
        "--third-party",
        action="store_true",
        help="Enable third-party dependency detection and recursion",
    )
    scan_parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="Skip analysis phase",
    )
    scan_parser.add_argument(
        "--fallback-tree",
        action="store_true",
        help=(
            "Enable fallback license tree: connect all unparsed files and isolated nodes "
            "to a synthetic root so license comparison can still proceed"
        ),
    )
    scan_parser.add_argument(
        "-c",
        "--config",
        help=(
            "Optional graph-build configuration. Can be a path to a TOML/JSON "
            "file (e.g. config.toml, config.json) or an inline TOML/JSON "
            "string. When omitted, built-in defaults are used."
        ),
    )
    scan_parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help=(
            "Reserved for future global timeout enforcement (currently unused; "
            "task-level timeouts still apply)."
        ),
    )
    scan_parser.add_argument(
        "--log-file",
        help="Output log to file (optional). When specified, logs are written to this file in addition to console.",
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
        "--work-dir",
        help=(
            "Base directory where graphs were stored during scan "
            "(expects graphs under <work-dir>/graphs). "
            "If not provided, defaults to .depanalyzer_cache/graphs."
        ),
    )
    export_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "asset_artifact"],
        default="json",
        help="Output format (default: json, use asset_artifact for asset->artifact mapping)",
    )
    export_parser.add_argument(
        "--with-deps",
        action="store_true",
        help="Include dependency graphs in export",
    )

    # ScanCode license command
    scancode_parser = subparsers.add_parser(
        "scancode",
        help="Run ScanCode to build license expression map (direct path or cached graphs)",
    )
    scancode_parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output JSON file containing {graph_node_id: spdx_license_expression}",
    )
    scancode_parser.add_argument(
        "--path",
        help=(
            "Scan this directory directly with ScanCode (no dependency on prior "
            "scan caches). Output format matches the graph-based mode."
        ),
    )
    scancode_parser.add_argument(
        "--cache-dir",
        help=(
            "Cache directory where graphs were stored during scan. "
            "You can point this to the per-project cache root "
            "(<cache-dir>/<source_stem>) or directly to its graphs/ folder. "
            "If not provided, defaults to .dep_cache."
        ),
    )
    scancode_parser.add_argument(
        "--source",
        help=(
            "Source path passed to the scan command. When provided, graphs are "
            "resolved under <cache-dir>/<source_stem>/graphs (mirrors scan layout)."
        ),
    )
    scancode_parser.add_argument(
        "-t",
        "--third-party",
        action="store_true",
        help="Include third-party dependency graphs in ScanCode scanning",
    )
    scancode_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-scan even if cached license maps exist",
    )

    # Global DAG inspection / validation command
    dag_parser = subparsers.add_parser(
        "dag",
        help="Inspect and validate the global package-level DAG",
    )
    dag_parser.add_argument(
        "--cache-dir",
        help=(
            "Cache directory where graphs were stored during scan "
            "(expects graphs under <cache-dir>/graphs). "
            "Typically the same <cache-dir>/<source_stem> used with the scan "
            "command. If not provided, defaults to .dep_cache."
        ),
    )
    dag_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help=(
            "Maximum number of cycles to report (default: 20). "
            "Use <=0 for no limit (may be expensive on large DAGs)."
        ),
    )
    dag_parser.add_argument(
        "--fail-on-cycle",
        action="store_true",
        help=(
            "Exit with non-zero status when dependency cycles are found. "
            "Useful for CI validation."
        ),
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

    if getattr(args, "install", False) is not False:
        install_version = None if args.install == "" else str(args.install)
        try:
            path = install_scancode(version=install_version)
            logger.info("ScanCode installed at %s", path)
        except RuntimeError as exc:
            logger.error(str(exc))
            return 1
        if not args.command:
            return 0

    # Dispatch to subcommand
    if args.command == "scan":
        return scan_command(args)
    elif args.command == "export":
        return export_command(args)
    elif args.command == "scancode":
        return scancode_command(args)
    elif args.command == "dag":
        return dag_command(args)
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
