# DepAnalyzer Refactoring Plan

## Overview
This document outlines the plan to refactor the `runtime` and `graph` modules to improve separation of concerns, maintainability, and architectural clarity.

## Part 1: Runtime Module Refactoring
**Goal:** Decouple strategy definitions and implementations from the core runtime orchestration logic.

### 1.1 Structure Reorganization
Create a new sub-package `depanalyzer/runtime/policies/` to house all policy-related code.

*   **Current Location**: `depanalyzer/runtime/`
*   **Target Location**: `depanalyzer/runtime/policies/`

| Current File | New File | Description |
| :--- | :--- | :--- |
| `strategies.py` | `protocols.py` | Defines the abstract interfaces (Protocols) for the runtime. |
| `default_strategies.py` | `defaults.py` | Contains the default implementations for the defined protocols. |
| `fallback_strategy.py` | `file_completeness.py` | Contains the logic for discovering untracked files (see renaming below). |

### 1.2 Renaming & Clarification
The term "Fallback" is currently ambiguous. It is effectively a strategy to ensure **Graph Completeness** by adding files that were not covered by specific parsers.

*   **Rename Class**: `FallbackJoinStrategy` -> `FileCompletenessJoinPolicy` (or `UntrackedFileDiscoveryPolicy`).
*   **Rationale**: This clearly indicates its purpose: scanning the file system to ensure every file node exists in the graph, regardless of whether it was parsed.

### 1.3 命名约定：Strategy vs. Policy
**决定：采纳 "Policy" 作为命名方案。**

*   **Policy (方针)**：此命名方案更好地贴合项目语境，将可替换的行为逻辑视为不同的“方针”或“策略集合”。
*   **设计模式说明**：从严格的设计模式角度来看，这些组件实现的是 GoF 的 **Strategy Pattern** (策略模式)，它强调“封装可互换的算法”。然而，"Policy" 在软件工程中也被广泛接受，尤其是在更强调业务规则或配置层面的场景。我们选择 "Policy" 是为了更好地服务项目自身的命名习惯和团队理解。

---

## Part 2: Graph Module Refactoring
**Goal:** Adopt a 3-tier architecture to separate Data Models, Core Management, and High-Level Operations.

### 2.1 New Directory Structure
The `depanalyzer/graph/` directory will be split into three semantic layers.

#### Layer 1: Models (`depanalyzer/graph/models/`)
*Focus: Pure data definitions, schema validation, and types. No complex logic.*

*   `schema.py` (Nodes, Edges, Pydantic models)
*   `identifiers.py` (Node ID generation rules)
*   `linking.py` (Edge metadata definitions)
*   `schema_utils.py`

#### Layer 2: Core (`depanalyzer/graph/core/`)
*Focus: State management, storage, and the primary API Facade.*

*   `manager.py` (The `GraphManager` facade)
*   `backend.py` (NetworkX or internal storage implementation)
*   `proxy.py` (Node/Edge proxy objects)
*   `contracts/` (Optional: move `contract.py` and `registry.py` here if they regulate core behavior)

#### Layer 3: Operations (`depanalyzer/graph/ops/`)
*Focus: Complex algorithms, transformations, and calculations on top of the graph.*

*   `condensation.py` (Graph simplification/folding)
*   `projection.py` (Generating specific views of the graph)
*   `merge.py` (Graph union logic)
*   `global_dag.py` (Cycle detection and DAG enforcement)

### 2.2 Implementation Steps
1.  **Create Directories**: Initialize `models`, `core`, and `ops` with `__init__.py` files.
2.  **Move Files**: Relocate files according to the mapping above.
3.  **Fix Imports**:
    *   Internal imports within `graph` need to become relative (e.g., `from ..models import schema`).
    *   Update `depanalyzer/graph/__init__.py` to re-export key classes (`GraphManager`, `NodeSpec`) so external consumers (like `runtime`) don't need to change their import paths.
4.  **Circular Dependency Check**: Pay close attention to imports between `core` and `ops`. `Ops` should depend on `Core` or `Models`, but `Core` should generally *not* depend on `Ops`.

## Part 3: Verification
1.  **Run Tests**: `pytest tests/` to ensure no regression.
2.  **Linting**: Check for import cycles and undefined references.
