#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static Dependency Analysis module for extracting dependencies from source code files.

This module provides tools for analyzing source code dependencies using static analysis
with tree-sitter parsing and regex fallbacks. It supports asynchronous processing
with event-driven result queues and parser caching for improved performance.
"""
import os
import re
import queue
import threading
from pathlib import Path
from typing import Set, Optional, List, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed, Future
import tree_sitter_cpp as tsc
from tree_sitter import Parser, Language


class BaseStaticDepExtractor:
    """
    Base class for static dependency extractors.

    Provides a common interface for extracting dependencies from source code files
    using static analysis techniques.

    Args:
        language (Optional[Language]): Tree-sitter language parser instance

    Methods:
        fallback_extract: Extract dependencies using regex when formal parsing fails
        parse: Parse a file and extract its dependencies
    """

    parser_type: str

    def __init__(self, language: Optional[Language] = None):
        if Parser is not None and language is not None:
            self.parser = Parser(language)
        else:
            self.parser = None

        self.supported_extensions: Set[str] = set()

    def can_handle_file(self, file_path: str) -> bool:
        """
        Router method to check if this extractor can handle the given file.

        Checks if the file extension is in the supported extensions set.

        Args:
            file_path (str): Path to the file to check

        Returns:
            bool: True if the file can be handled, False otherwise
        """
        if not self.supported_extensions:
            return True

        file_ext = Path(file_path).suffix.lower()
        return file_ext in self.supported_extensions

    def fallback_extract(self, content: str) -> dict:
        """
        Extract dependencies using regex patterns as fallback method.

        Called when tree-sitter parsing fails or is unavailable.

        Args:
            content (str): Source code content to analyze

        Returns:
            dict: Dictionary containing extracted dependency information
        """
        raise NotImplementedError


class CDepExtractor(BaseStaticDepExtractor):
    """
    C/C++ dependency extractor using tree-sitter and regex parsing.

    Extracts include dependencies from C/C++ source files using tree-sitter
    for precise AST parsing with regex fallback for compatibility.

    Args:
        language (Optional[Language]): Tree-sitter C/C++ language parser

    Methods:
        fallback_extract: Extract includes using regex patterns
        parse: Parse C/C++ files and extract include dependencies
    """

    parser_type = "c"

    def __init__(self, language: Optional[Language] = None):
        if language is None:
            language = Language(tsc.language())
        super().__init__(language)
        self.include_pattern = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
        self.supported_extensions = {".c", ".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".h++"}

    def fallback_extract(self, content: str) -> dict:
        """
        Extract include dependencies using regex patterns.

        Fallback method when tree-sitter parsing is unavailable or fails.

        Args:
            content (str): C/C++ source code content to analyze

        Returns:
            dict: Dictionary with 'includes' key containing list of include paths
        """
        try:
            includes = set(self.include_pattern.findall(content))
            return {"includes": [Path(include) for include in includes]}
        except Exception as e:
            return {"error": str(e)}

    def _parse_includes_from_tree(self, tree, content: str) -> Set[str]:
        """
        Extract include dependencies from tree-sitter AST.

        Uses tree-sitter queries to precisely extract #include directives
        from the parsed AST with regex fallback on query failures.

        Args:
            tree: Tree-sitter parse tree object
            content (str): Source code content for byte offset resolution

        Returns:
            Set[str]: Set of include file paths found in the source
        """
        includes = set()
        query_str = """
        (preproc_include
            path: [(string_literal) (system_lib_string)] @path
        )
        """
        try:
            query = self.parser.language.query(query_str)
            captures = query.captures(tree.root_node)
            for node, _ in captures:
                include_text = content[node.start_byte : node.end_byte]
                include_path = include_text.strip('"<>')
                if include_path:
                    includes.add(include_path)
        except (AttributeError, RuntimeError):
            includes = set(self.include_pattern.findall(content))
        return includes

    def parse(self, file_path: str) -> dict:
        """
        Parse C/C++ file and extract include dependencies.

        Reads the specified file and extracts #include dependencies using
        tree-sitter parsing with regex fallback for robustness.

        Args:
            file_path (str): Path to the C/C++ source file to analyze

        Returns:
            dict: Parse result containing file path, includes list, and metadata
        """
        try:
            content_bytes = Path(file_path).read_bytes()
            content = content_bytes.decode("utf-8", errors="ignore")

            result = {"file": file_path}

            if self.parser is None:
                fallback_result = self.fallback_extract(content)
                return {**result, "root": "fallback", **fallback_result}

            try:
                tree = self.parser.parse(content_bytes)
                includes = self._parse_includes_from_tree(tree, content)
                return {**result, "root": tree.root_node.type, "includes": [Path(include) for include in includes]}
            except (UnicodeDecodeError, OSError):
                fallback_result = self.fallback_extract(content)
                return {**result, "root": "fallback", **fallback_result}

        except (OSError, UnicodeDecodeError) as e:
            return {"file": file_path, "error": str(e)}


# Process-level parser cache for worker reuse
_PARSER_CACHE = {}


def _get_parser(parser_type: str) -> BaseStaticDepExtractor | None:
    """
    Get or create cached parser instance for worker process.

    Reuses parser instances within worker processes to avoid
    repeated initialization overhead.

    Args:
        parser_type (str): Type of parser ("c" or other)

    Returns:
        BaseStaticDepExtractor: Cached parser instance
    """
    if parser_type not in _PARSER_CACHE:
        if parser_type == CDepExtractor.parser_type:
            _PARSER_CACHE[parser_type] = CDepExtractor()
        else:
            return None

    return _PARSER_CACHE[parser_type]


def _worker_dispatch(task_data: Tuple[str, List[str]]) -> dict:
    """
    Worker process task dispatcher function.

    Dispatches parsing tasks to cached parser instances in worker processes.
    Reuses parser instances to avoid repeated initialization overhead.

    Args:
        task_data (Tuple[str, str]): Tuple containing:
            - file_path: Path to file to parse
            - parser_type: Type of parser to use ("c" or other)

    Returns:
        dict: Parse result dictionary from the cached parser or skip result for unsupported files
    """
    file_path, parser_type = task_data

    # Get cached parser instance for this worker process
    parser = _get_parser(parser_type)

    if parser is not None and parser.can_handle_file(file_path):
        return parser.parse(file_path)

    return {"file": file_path, "skipped": True, "reason": "Unsupported file extension"}


class ResultIterator:
    """
    Event-driven iterator for real-time result processing.

    Provides a simple for-loop interface to consume results as they become available.
    Uses event-driven approach to wait for new results efficiently.
    """

    def __init__(self, pool):
        """
        Initialize result iterator.

        Args:
            pool (AsyncParserPool): The parser pool to iterate over
        """
        self.pool = pool

    def __iter__(self):
        """Return self as iterator."""
        return self

    def __next__(self):
        """
        Get next result from pool.

        Uses event-driven approach to wait for results. Will block until
        a result is available. Only raises StopIteration when the pool
        is sealed AND all tasks are completed.

        Returns:
            dict: Next parsing result

        Raises:
            StopIteration: When pool is sealed and all tasks are completed
        """
        while True:
            # Try to get existing result first (non-blocking)
            result = self.pool.get_result(block=False)
            if result is not None:
                return result

            # Only check for completion if pool is sealed
            if self.pool.is_sealed():
                if self.pool.get_pending_count() == 0 and self.pool.get_active_count() == 0:
                    result = self.pool.get_result(block=False)
                    if result is not None:
                        return result
                    raise StopIteration

            # Wait for new result to be available (event-driven)
            if not self.pool.wait_for_result(timeout=1.0):
                # Timeout occurred, continue loop to check task status again
                continue


class AsyncParserPool:
    """
    Asynchronous parser pool with real-time result processing via event-driven queue.

    Provides high-performance parallel parsing of source files with real-time
    result queue access and background processing.

    Args:
        max_workers (int): Maximum number of worker processes

    Methods:
        start: Start the parser pool in background
        stop: Stop the parser pool and cleanup resources
        add_file: Add single file to parsing queue
        add_files: Add multiple files to parsing queue
        get_result: Get next result from queue (blocking/non-blocking)
        wait_for_completion: Wait for all tasks to complete
        get_pending_count: Get number of pending tasks
        get_active_count: Get number of active tasks
        is_running: Check if pool is running
    """

    def __init__(self, max_workers: int = None):
        self.max_workers = max_workers or os.cpu_count()

        self.task_queue = queue.Queue()  # (file_path,)
        self.result_queue = queue.Queue()  # Results from workers

        self._stop_event = threading.Event()
        self._new_task_event = threading.Event()  # New task notification event
        self._completion_event = threading.Event()  # All tasks completed event
        self._result_available_event = threading.Event()  # New result available event
        self._running = False
        self._sealed = False  # Flag to control when iteration can stop

        self._worker_thread = None
        self._executor = None

        self._active_futures: List[Future] = []  # Active futures list

    def _check_completion(self):
        """
        Check if all tasks are completed and set completion event.

        Sets completion event when both pending and active task counts are zero.
        Should be called after task submission and completion.
        """
        if self.get_pending_count() == 0 and self.get_active_count() == 0:
            self._completion_event.set()
        else:
            self._completion_event.clear()

    def _wait_for_events(self, timeout: float = 1.0):
        """
        Wait for task completion or new task events efficiently.

        Args:
            timeout (float): Maximum time to wait for events
        """
        if not self._active_futures:
            return

        try:
            # Wait for any task to complete, but not exceeding timeout
            completed = as_completed(self._active_futures, timeout=timeout)
            next(completed)  # Take only the first completed, then handle by _collect_completed_results
        except StopIteration:
            # No tasks completed
            pass
        except (OSError, RuntimeError, TimeoutError):
            # Handle specific exceptions that can occur during future operations
            pass

    def _worker_loop(self):
        """
        Main loop for background worker thread.

        Uses event-driven approach instead of polling for better efficiency.
        Waits for new tasks or completed futures rather than busy waiting.
        """
        self._executor = ProcessPoolExecutor(max_workers=self.max_workers)

        try:
            while not self._stop_event.is_set():
                # Submit all pending tasks
                self._submit_pending_tasks()

                # Collect completed results (non-blocking)
                self._collect_completed_results()

                # If there are active tasks, wait for completion or new tasks
                if self._active_futures:
                    self._wait_for_events(timeout=1.0)  # 1 second timeout
                else:
                    # When no active tasks, wait for new task events
                    self._new_task_event.wait(timeout=1.0)
                    self._new_task_event.clear()

            # Process remaining tasks
            self._submit_pending_tasks()
            self._wait_for_completion()

        finally:
            if self._executor:
                self._executor.shutdown(wait=True)

    def _submit_pending_tasks(self):
        """
        Submit pending tasks from queue to worker processes.

        Processes all tasks in the queue and submits them to the process pool
        with appropriate parser type detection. Filters out unsupported file
        extensions and puts skip results directly into the result queue.
        """
        while not self.task_queue.empty():
            try:
                file_path = self.task_queue.get_nowait()
                future = self._executor.submit(_worker_dispatch, (file_path, "c"))
                self._active_futures.append(future)

            except queue.Empty:
                break

        # Check if all tasks are completed
        self._check_completion()

    def _collect_completed_results(self):
        """
        Collect results from completed tasks and push to result queue.

        Checks all active futures for completion and puts results in queue
        with event notification for real-time access.
        """
        completed_futures = []

        for future in list(self._active_futures):
            if future.done():
                try:
                    result = future.result()
                    self.result_queue.put(result)
                    self._result_available_event.set()  # Notify that result is available
                except (OSError, RuntimeError, ValueError, TypeError) as e:
                    error_result = {"error": f"Processing failed: {str(e)}"}
                    self.result_queue.put(error_result)
                    self._result_available_event.set()

                completed_futures.append(future)

        # Remove completed futures and clean up parameter mapping
        for future in completed_futures:
            self._active_futures.remove(future)

        # Check if all tasks are completed
        self._check_completion()

    def _wait_for_completion(self):
        """
        Wait for all active tasks to complete.

        Blocks until all submitted futures are finished and
        puts results in result queue.
        """
        for future in as_completed(self._active_futures):
            try:
                result = future.result()
                self.result_queue.put(result)
                self._result_available_event.set()
            except (OSError, RuntimeError, ValueError, TypeError) as e:
                error_result = {"error": f"Processing failed: {str(e)}"}
                self.result_queue.put(error_result)
                self._result_available_event.set()

        self._active_futures.clear()

        # Check if all tasks are completed
        self._check_completion()

    def start(self):
        """
        Start the parser pool in background (non-blocking).

        Initializes worker thread and begins processing tasks from queue.
        Can be called multiple times safely (no-op if already running).
        """
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._completion_event.clear()  # Reset completion state
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        print("Parser pool started in background")

    def add_file(self, file_path: str):
        """
        Add single file to parsing queue (can be called at runtime).

        Args:
            file_path (str): Path to source file to parse
        """
        self._sealed = False
        self.task_queue.put(file_path)
        # Clear completion event since we have new tasks
        self._completion_event.clear()

        self._new_task_event.set()

    def add_files(self, file_paths: List[str]):
        """
        Add multiple files to parsing queue in batch.

        Args:
            file_paths (List[str]): List of source file paths to parse
        """
        self._sealed = False
        for file_path in file_paths:
            self.task_queue.put(file_path)

        # Clear completion event and notify worker thread after batch addition
        if file_paths:
            self._completion_event.clear()
            self._new_task_event.set()

    def stop(self, wait: bool = True):
        """
        Stop the parser pool and cleanup resources.

        Args:
            wait (bool): Whether to wait for worker thread to finish
        """
        if not self._running:
            return

        self._stop_event.set()

        if wait and self._worker_thread:
            self._worker_thread.join()

        self._running = False

    def get_pending_count(self) -> int:
        """
        Get number of pending tasks in queue.

        Returns:
            int: Number of tasks waiting to be processed
        """
        return self.task_queue.qsize()

    def get_active_count(self) -> int:
        """
        Get number of currently active tasks.

        Returns:
            int: Number of tasks currently being processed
        """
        return len(self._active_futures)

    def get_result(self, block: bool = True, timeout: Optional[float] = None) -> Optional[dict]:
        """
        Get next result from result queue.

        Args:
            block (bool): Whether to block waiting for result
            timeout (Optional[float]): Maximum time to wait if blocking

        Returns:
            Optional[dict]: Parse result or None if no result available (non-blocking)

        Raises:
            queue.Empty: If no result available within timeout (blocking mode)
        """
        if block:
            return self.result_queue.get(timeout=timeout)
        else:
            try:
                return self.result_queue.get_nowait()
            except queue.Empty:
                return None

    def wait_for_result(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for new result to be available using event-driven approach.

        Args:
            timeout (Optional[float]): Maximum time to wait for result

        Returns:
            bool: True if result is available, False if timeout occurred
        """
        # Clear the event first, then check if results are already available
        if not self.result_queue.empty():
            return True

        self._result_available_event.clear()
        return self._result_available_event.wait(timeout=timeout)

    def results(self):
        """
        Get an iterator for real-time result processing.

        Returns an event-driven iterator that yields results as they become available.
        Perfect for use in for-loops to process results synchronously.

        Returns:
            ResultIterator: Iterator for processing results in real-time

        Example:
            for result in pool.results():
                print(f"Processed: {result.get('file')}")
                if 'includes' in result:
                    print(f"  Found {len(result['includes'])} includes")
        """
        return ResultIterator(self)

    def is_running(self) -> bool:
        """
        Check if parser pool is currently running.

        Returns:
            bool: True if pool is active, False otherwise
        """
        return self._running

    def seal(self):
        """
        Seal the parser pool to indicate no more tasks will be added.

        This must be called manually by the user before the ResultIterator
        will raise StopIteration when all tasks are completed.
        """
        self._sealed = True

    def is_sealed(self) -> bool:
        """
        Check if the parser pool has been sealed.

        Returns:
            bool: True if pool is sealed, False otherwise
        """
        return self._sealed

    def wait_for_completion(self, timeout: Optional[float] = None):
        """
        Wait for all tasks to complete (blocking).

        Uses event-driven approach to wait for completion rather than polling.
        Blocks until both pending and active task counts reach zero.

        Args:
            timeout (Optional[float]): Maximum time to wait in seconds

        Raises:
            TimeoutError: If timeout is exceeded before completion
        """
        # Initial check - might already be completed
        self._check_completion()

        # Wait for completion event
        if not self._completion_event.wait(timeout=timeout):
            raise TimeoutError("Wait for completion timed out")
