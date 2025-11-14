- 先给个总体结论：
这次提交已经把我上次指出的大部分**架构级 P0 问题**修掉了：Workspace/Transaction/Coordinator 协议对齐、CMake GraphBuilder 适配新 GraphManager、pyproject 入口修正、Hvigor/CMake Detector/Parser 重写、图序列化 `_flush_graph_to_disk()` 都已经落地   。

但是离你描述的那套“**任意混编项目 → 资产→产物映射图 + 第三方依赖多图联动**”还有几处关键缺口，尤其是：

1. 生态注册现在有了，但 **CLI 根本没触发 `depanalyzer.parsers` 的 import**，导致 DETECT/PARSE 仍然是空跑。
2. HvigorParser 虽然迁到 `depanalyzer.*`，但用的是 GraphManager 里的 `NodeType/EdgeKind`，而它们目前**没有 CONFIG / EXTERNAL_LIBRARY / DEFINED_BY 常量**，Hvigor 生态仍然不能稳定注册。
3. Graph 序列化已经完成，但 **没有把 graph_id → cache_path 写入 GraphRegistry**，`scan` 命令里查不到缓存。
4. `_discover_dependencies()` / `_link_dependency_graph()` / `_phase_analyze()` 仍是 stub，第三方依赖展开、多 GraphManager + GraphRegistry + GlobalDAG 还没接上。

下面我按“已修好的”和“仍然阻塞/缺失的”两块来讲，然后给一份可以直接照着改的小清单。

---

## 一、这次提交已经明显改进 / 修复的点

### 1. Workspace / Transaction 协议修正

* `Workspace` 已经增加了 `root_path` property，并在 `acquire()` 设置 `_root_path` 后通过该 property 访问。
* `Transaction._phase_acquire()` 里不再自己维护 root_path，而是统一用 `self.workspace.acquire()` / `self.workspace.root_path`，GraphManager 初始化时也传入 `root_path=self.workspace.root_path`。

→ 之前的 `AttributeError: Workspace has no attribute root_path` 已经不存在。

### 2. Transaction.run ↔ Coordinator.worker 返回类型统一 + 图序列化

* `Transaction.run()` 现在固定返回 `TransactionResult`，内部完整地跑完所有 Phase，并在成功路径下调用 `_flush_graph_to_disk()` 将图序列化到 `.depanalyzer_cache/graphs/{graph_id}.json`。
* `_flush_graph_to_disk()` 使用 NetworkX `node_link_data` 存储原生 MultiDiGraph，并在 metadata 中记录 `graph_id / node_count / edge_count / source / timestamp`。
* `TransactionCoordinator._run_transaction_worker()` 现在只做 `result = transaction.run()` 并返回 `TransactionResult`，不再假定 `run()` 返回 GraphManager，也不再二次构造结果。

→ 之前那个“worker 把 TransactionResult 当 GraphManager 调 `.node_count()`”的致命错误已经消失。

### 3. CMakeGraphBuilder 适配新的 GraphManager API + 契约注册

* 所有 `add_node` 调用都改成了 `(node_id, node_type, confidence=..., **attrs)` 的新风格，如 target/source/external library 都把 `node_type` 作为第二个位置参数传入。
* `CMAKE_LINK_DEPENDENCY_FOUND`、`CMAKE_INCLUDE_DIRS_DECLARED` 等事件均用 `edge_kind="link_libraries"` / `"sources"` / `"include_dirs"` 字符串，与 `GraphManager.derive_asset_artifact_projection()` 预期的 edge kind 完全对齐 。
* shared library target 现在在 `_handle_target_created` 里调用 `_register_provider_contract()`，把 CMake 产物注册为 provider 契约供 JOIN 阶段 `ContractMatcher` 使用。

→ CMake 侧中间节点（subdirectory/target/source/lib）+ 契约注册路径现在是跑得通的。

### 4. Hvigor Detector / Parser 迁移到了新架构

* HvigorDetector 现在继承 `BaseDetector`，使用 `TARGET_PATTERNS` 扫描 `build-profile.json5` / `oh-package.json5` / `module.json5` / `lock` / `hvigor-config`，并向 EventBus 发布 `TARGET_DETECTED` 事件。
* HvigorParser 迁移到 `depanalyzer.parsers.hvigor.config_parser.HvigorParser`，继承新 `BaseParser`，统一使用 `self.add_node/add_edge` 直接操作 GraphManager，并通过 EventBus 发布 `MODULE_PARSED` / `DEPENDENCY_DISCOVERED` / `HVIGOR_NATIVE_DIR_FOUND` 等事件。
* 在处理 `oh-package.json5` 的 native 依赖时，已经会解析 types「桥接」的 `.d.ts`，创建 code 节点并注册 consumer 侧契约（ARM ABI 三个变体），埋好了和 CMake 契约匹配的所有信息 。

→ Hvigor 这块的逻辑从“旧包路径 + 旧 BaseParser”升级到了新架构，只差类型系统统一（下面会讲）。

### 5. 生态注册中心 + parsers 注册逻辑

* `EcosystemRegistry` 已完整支持 detector/parser/code_parser/dep_fetcher 的注册、查询和 `register_ecosystem` 便捷方法。
* `depanalyzer/parsers/__init__.py` 中已经尝试导入 CPP 和 Hvigor 生态的 Detector/Parser/DepFetcher/CodeParser，并在导入成功时调用 `register_ecosystem` 注册。
* 如果某个生态导入失败会记录 warning，并只注册成功的那一部分。

→ 从架构上，生态注册已经具备“自动发现 + 单例注册表”的形状。

### 6. CLI 与 Poetry 入口修正 + export 骨架

* `pyproject.toml` 的脚本入口已经指向 `depanalyzer.main:main`，不再引用不存在的 `app.run`。
* `depanalyzer/cli/export.py` 已新增 `export_command`，虽然目前只是 `NotImplementedError`，但至少 main.py 在 import 时不会崩溃。

→ 安装为包后运行 `depanalyzer` 不再是直接 import 错误，而是功能上的 TODO。

---

## 二、当前仍然存在的阻塞 / 功能缺口

我认为现在的 P0 / 关键问题主要有四类：

### 问题 1：生态注册没有自动触发，DETECT/PARSE 仍然是空跑

事实：

* `Transaction._phase_detect()` 通过 `EcosystemRegistry.list_ecosystems()` 获取所有已注册的生态，如果集合为空会直接 log “No ecosystems registered, skipping detection” 并返回。
* `parsers/__init__.py` 虽然定义了注册逻辑，但这个模块**永远不会被 import**，因为：

  * `depanalyzer/main.py` 只 import 了 `scan_command` 和 `export_command`，没有 import `depanalyzer.parsers`。
  * scan 逻辑里也没有任何地方触及 parsers 包。

结果：

* 实际运行 `depanalyzer scan ...` 时，`EcosystemRegistry` 中完全没有 detector/parser，被动跳过 DETECT/PARSE，后面所有逻辑都在“没有目标”的前提下运行：

  * `_phase_parse()` 会因为 `_detected_targets` 为空直接返回；
  * `_phase_join()` / `_phase_analyze()` 也因为 `_graph_manager` 未初始化而被跳过；
  * `run()` 最后为了构造 `TransactionResult` 会创建一个空的 GraphManager，然后 `_flush_graph_to_disk()` 发现 node_count=0 直接跳过序列化。

→ **即使 CMake/Hvigor 生态本身没问题，目前 CLI 实际上什么都不会解析**，这是功能级别的 P0。

**建议修复：**

在 `depanalyzer/main.py` 的顶部增加一次性 import，确保 parsers 包加载并注册生态：

```python
# main.py 顶部
import argparse
import logging
import sys
from pathlib import Path

import depanalyzer.parsers  # 触发 CPP/Hvigor 生态注册
from depanalyzer.cli.scan import scan_command
from depanalyzer.cli.export import export_command
```

这样 `Transaction._phase_detect()` 再调用 `list_ecosystems()` 时，就能看到至少 `"cpp"`（以及修好 Hvigor 后的 `"hvigor"`）。

---

### 问题 2：HvigorParser 使用的 NodeType / EdgeKind 与 GraphManager 不一致

事实：

* HvigorParser 现在从 `depanalyzer.graph.manager` 导入 `NodeType, EdgeKind`，在代码里使用了：

  * `NodeType.CONFIG`（创建配置文件节点）
  * `NodeType.MODULE`（module.json5 和 build-profile 中的 module）
  * `NodeType.EXTERNAL_LIBRARY`（ext_lib:* 节点）
  * `EdgeKind.DEFINED_BY`（module → config 之间的定义关系）
* 但 GraphManager 里的 NodeType/EdgeKind 定义只有：

  * NodeType：`ASSET/PROCESS/TARGET/ARTIFACT/BUILD_CONFIG/EXTERNAL_DEP/TOOLCHAIN/PROXY + CODE/HEADER/PROJECT_HEADER/SYSTEM_HEADER/SHARED_LIBRARY/STATIC_LIBRARY/EXECUTABLE/MODULE`，**没有 CONFIG / EXTERNAL_LIBRARY**。
  * EdgeKind：`CONSUMES/PRODUCES/DEPENDS_ON/LINKS/CONTAINS/DECLARES + SOURCES/INCLUDE/IMPORT/PART_OF/AFFECTS/LINK_LIBRARIES`，**没有 DEFINED_BY / INCLUDE_DIRS 等**。

结果：

* 只要 HvigorParser 模块被 import，Python 会在第一次访问 `NodeType.CONFIG` 或 `NodeType.EXTERNAL_LIBRARY` 时抛 `AttributeError`，导致 `depanalyzer/parsers/__init__.py` 中 Hvigor 生态导入失败，`_HVIGOR_AVAILABLE = False`，从而 Hvigor 生态不会注册。
* 也就是说：**Hvigor 路径目前仍然完全不可用**，虽然代码结构已经迁移，但类型系统没对齐。

**建议修复（推荐统一 NodeType/EdgeKind 为 GraphManager 一套）：**

在 `depanalyzer/graph/manager.py` 的 `NodeType` 和 `EdgeKind` 中补齐 core.schema 里的常量：

```python
# depanalyzer/graph/manager.py

class NodeType:
    """Node type constants."""

    ASSET = "asset"
    PROCESS = "process"
    TARGET = "target"
    ARTIFACT = "artifact"
    BUILD_CONFIG = "build_config"
    EXTERNAL_DEP = "external_dep"
    TOOLCHAIN = "toolchain"
    PROXY = "proxy"

    # Newer semantic types (对齐 core.schema)
    CONFIG = "config"
    EXTERNAL_LIBRARY = "external_library"
    SUBDIRECTORY = "subdirectory"

    # Legacy types for backward compatibility
    CODE = "code"
    HEADER = "header"
    PROJECT_HEADER = "project_header"
    SYSTEM_HEADER = "system_header"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"
    MODULE = "module"
```

```python
class EdgeKind:
    """Edge kind constants."""

    CONSUMES = "consumes"
    PRODUCES = "produces"
    DEPENDS_ON = "depends_on"
    LINKS = "links"
    CONTAINS = "contains"
    DECLARES = "declares"

    # Additional semantic kinds (对齐 core.schema)
    DEFINED_BY = "defined_by"
    INCLUDE_DIRS = "include_dirs"
    ALIAS_OF = "alias_of"
    IMPLEMENTS_NATIVE = "implements_native"

    # Legacy kinds
    SOURCES = "sources"
    INCLUDE = "include"
    IMPORT = "import"
    PART_OF = "part_of"
    AFFECTS = "affects"
    LINK_LIBRARIES = "link_libraries"
```

这样 HvigorParser 和 HvigorCMakeBridge 在使用 `NodeType.CONFIG/MODULE/EXTERNAL_LIBRARY`、`EdgeKind.DEFINED_BY` 时就不会再打到不存在的常量，GraphManager 也能理解这些 type/kind（只当作字符串，不会破坏已有逻辑）。

> 备选方案是：HvigorParser 改成从 `depanalyzer.core.schema` 导入 NodeType/EdgeKind，但考虑到 GraphManager 已经被定位成“唯一真源”，建议直接把 NodeType/EdgeKind 的全集迁到 graph.manager 里。

---

### 问题 3：GraphRegistry 尚未接入 Transaction.run / CLI 扫描

事实：

* GraphRegistry 基于 `multiprocessing.Manager().dict()` 和 `Lock` 实现了跨进程的 `graph_id → {"cache_path": ..., "summary": ...}` 映射，`register/get_cache_path/get_summary` 已经写好。
* `Transaction.run()` 成功时虽然调用了 `_flush_graph_to_disk()` 写 JSON 文件，但**没有任何地方调用 `GraphRegistry.register()`** 把 graph_id 注册进去。
* `cli/scan.py` 在事务结束后，会通过 `GraphRegistry().get_cache_path(result.graph_id)` 去找缓存文件，然后输出“Graph cached at: ...”或“Graph not found in registry: ...”。

结果：

* 当前实现里 Registry 只会被加载一次、读 `registry.json`，但没有任何地方写入新图；`scan` 命令永远落在 “Graph not found in registry” 分支，即使 `_flush_graph_to_disk()` 已经成功写了 `.depanalyzer_cache/graphs/<graph_id>.json`。

**建议修复（最小化且符合你多图设计的方向）：**

在 `Transaction.run()` 成功路径里，在构造 `TransactionResult` 之前注册 Graph：

```python
from depanalyzer.graph.registry import GraphRegistry  # 放在函数顶部 import

# ... run() 成功路径中，_flush_graph_to_disk() 之后：
cache_path = self._flush_graph_to_disk()
if cache_path:
    logger.info("Graph cached at: %s", cache_path)
    try:
        from depanalyzer.graph.registry import GraphRegistry
        registry = GraphRegistry()
        summary = self._graph_manager.get_summary()
        registry.register(self.graph_id or "unknown", cache_path, summary)
    except Exception as reg_err:
        logger.error("Failed to register graph in GraphRegistry: %s", reg_err)
```

这样 CLI 的 `scan` 就可以通过 GraphRegistry 找到缓存文件，后续你在 `export_command` 或 `scan` 里真正做导出/全局 DAG 时才有基础。

---

### 问题 4：第三方依赖展开、多 GraphManager + GlobalDAG 仍未接线

事实：

* `_phase_resolve_deps()` 会调用 `_discover_dependencies()`，对返回的每个 `dep` 构造子 Transaction 并提交到 `TransactionCoordinator`，等待 child 事务完成后调用 `_link_dependency_graph(result.graph_id, dep)`。
* 但 `_discover_dependencies()` 目前仍然只是记录 “Dependency discovery not yet implemented”，返回空列表。
* `_link_dependency_graph()` 也只是打印 log，没有实际在父图里创建 Proxy/External 节点，也没有写 GraphRegistry / GlobalDAG。
* HvigorParser 虽然在处理 oh-package/lock 时会发布 `EventType.DEPENDENCY_DISCOVERED` 事件并携带 `DependencySpec`，但没有任何 hook 或 Transaction 去收集这些事件。

→ **第三方依赖展开目前功能上仍为 0**，和你“多个 GraphManager、Markov 风格多层依赖传播”的设计完全没接上。

**建议（保留之前的设计，但可以更精简一点）：**

1. 在 Transaction 里增加一个简单的依赖收集列表 `self._discovered_deps: list[DependencySpec]`，再加一个 hook `DependencyCollector` 订阅 `DEPENDENCY_DISCOVERED` 事件，把 `event.data["spec"]` append 进去。
2. `_discover_dependencies()` 直接返回 `self._discovered_deps`；对只看 Hvigor 的场景已经足够。
3. 写一个简单版 `runtime/dependency_resolver.resolve_dependencies(specs)`，按 `spec.ecosystem` 从 `EcosystemRegistry` 找到 DepFetcher，拉到 `.depanalyzer_cache/deps`，返回 `[{"name": ..., "source": local_path, "ecosystem": spec.ecosystem}]`。
4. 在 `_phase_resolve_deps()` 用上述 resolver 结果创建子 Transaction，并在 `_link_dependency_graph()` 里：

   * 在父图里为每个依赖创建 `NodeType.PROXY` 或 `NodeType.EXTERNAL_DEP` 节点，记录 `child_graph_id/source_path/ecosystem/name/version`；
   * 把 `ext_lib:*` 等逻辑节点连接到这个 Proxy 节点；
   * 调用 `GlobalDAG.add_dependency(self.graph_id, child_graph_id)`（GlobalDAG 已实现）。

这一块还是你原始设计的“图与图之间仅通过一个节点相连”的核心，建议分阶段按这个思路实现。

---

### 问题 5：分析阶段（资产→产物投影 / deadcode / 动静态链接）仍未接入

事实：

* `_phase_analyze()` 目前仍然只是一条 log：“Analysis phase not yet implemented”。
* 但 `GraphManager.derive_asset_artifact_projection()` 已经实现了比较复杂的投影逻辑，包括：

  * 基于 `sources/contains/part_of` 生成 code→artifact 关系；
  * include 传播、link_libraries 闭包、header multi-hop 传播；
  * 多条 `projection` 边的融合（take max confidence + merge derived_from） 。
* `DeadcodeAnalyzer` 和 `LinkageAnalyzer` 也已经在 `depanalyzer/analysis` 下实现了到可运行的程度，只是没人调用。

→ 按你最初的需求，“deadcode / 动静态链接 / 资产→产物映射”现在仍然是**全未接入 lifecycle**，但这属于功能欠缺，不是 crash 级别。

**建议最小接入版本：**

在 `_phase_analyze()` 中：

```python
from depanalyzer.analysis.deadcode import DeadcodeAnalyzer
from depanalyzer.analysis.linkage import LinkageAnalyzer

def _phase_analyze(self) -> None:
    logger.info("=== Phase: %s ===", LifecyclePhase.ANALYZE)
    self._current_phase = LifecyclePhase.ANALYZE

    if not self._graph_manager:
        logger.warning("GraphManager not initialized, skipping analyze phase")
        return

    # 1) 资产→产物映射投影
    self._graph_manager.derive_asset_artifact_projection()

    # 2) deadcode 分析
    dc = DeadcodeAnalyzer(self._graph_manager)
    dead_nodes = dc.analyze()
    self._graph_manager.set_metadata("dead_nodes", list(dead_nodes))

    # 3) 链接类型分析（目前 LinkageAnalyzer 还能是空实现，后续补）
    try:
        from depanalyzer.analysis.linkage import LinkageAnalyzer
        la = LinkageAnalyzer(self._graph_manager)
        linkage_map = la.analyze()
        self._graph_manager.set_metadata("linkage_map", linkage_map)
    except ImportError:
        logger.info("LinkageAnalyzer not available, skipping linkage analysis")
```

这样至少可以保证你在 Stage 1 就能得到一张已经做过投影 + 判了 deadcode 的图，后续再细化链接类型判断 。

---

## 三、和你最初设计的 7 条需求 + 多 GraphManager 思路对照（更新版）

快速对照一下你最初列的几点需求，现在的状态：

1. **混编多语言解析**：

   * CMake（CPP 生态）已经基本接通（Detector + Parser + CMakeGraphBuilder + eventbus） ；
   * Hvigor 解析器结构已对齐，但由于 NodeType/EdgeKind 不一致 + parsers 未 import，目前在运行层面仍是“未启用”。
2. **第三方依赖拉取 + 非递归展开**：

   * HvigorDepFetcher / CppDepFetcher 已经写好 ；
   * Transaction 的子事务调度也就位；
   * 但 `_discover_dependencies()` / `_link_dependency_graph()` 仍是 stub → 功能层面未实现。
3. **高性能 / 低开销**：

   * Transaction 级多进程 + Task 级线程池 + CodeParserPool 全局进程池架构已经全部到位，和 docs 里的说明一致 。
   * 嵌套进程池的问题现在只在“深层第三方依赖”场景才会暴露，可以后续优化。
4. **合规安全（保守过估计）**：

   * GraphManager 节点/边已经都有 `over_approx` 属性；CMake link 边的 over_approx 也有传递。
   * 分析阶段尚未利用这些元信息做真正的合规模型。
5. **动态/静态链接、deadcode**：

   * 分析器类已经存在，但还没接入 `_phase_analyze()`。
6. **中间步骤作为逻辑节点（GN config / add_subdirectory）**：

   * CMake `add_subdirectory()` 现在明确创建 `subdirectory` 类型节点，GraphManager 投影里也对 subdirectory 做了专门处理 。
7. **Python + Poetry + 混编 C/Rust/Zig/Go 可扩展**：

   * GraphBackend 抽象 + Registry 架构都已经满足后续用 C/Rust/Go 替换热点的需求 。
8. **多 GraphManager + GraphRegistry + GlobalDAG（只保留一个图在内存）**：

   * GraphRegistry / GlobalDAG 的数据结构和 API 均已经实现 ；
   * Transaction.run 目前只做了本图序列化，没有注册 registry；子图链接 `_link_dependency_graph()` 尚未填充 → 多图 Markov 风格传播仍为骨架状态。

---

## 四、下一步建议的“可直接照着改”的实践清单

如果按你现在这个基础，想快速把“单仓库 + CMake/Hvigor 混编 + 图序列化 + 基本分析”打通，我建议按优先级做下面几步：

1. **立刻修的 P0：让 DETECT/PARSE 真正跑起来**

   * 在 `depanalyzer/main.py` 顶部加 `import depanalyzer.parsers`，确保生态注册发生。

2. **立刻修的 P0：统一 NodeType/EdgeKind，使 HvigorPath 可用**

   * 在 `graph/manager.py` 中补齐 `NodeType.CONFIG/EXTERNAL_LIBRARY/SUBDIRECTORY` 和 `EdgeKind.DEFINED_BY/INCLUDE_DIRS/ALIAS_OF/IMPLEMENTS_NATIVE`，对齐 core.schema 的定义 。
   * HvigorParser 可以继续使用 `NodeType.CONFIG/MODULE/EXTERNAL_LIBRARY` 和 `EdgeKind.DEFINED_BY`，不需要改动；CMake/Hvigor 桥接等模块也能依赖这套统一类型。

3. **近一步：把 GraphRegistry 接到 Transaction.run**

   * 在 `Transaction.run()` 成功路径中，如果 `_flush_graph_to_disk()` 返回了有效 `cache_path`，就调用 `GraphRegistry.register(self.graph_id, cache_path, self._graph_manager.get_summary())`。
   * 这样 `scan` 命令输出的 `Graph cached at: ...` 就是真实可用。

4. **近一步：在 `_phase_analyze()` 中接入投影 + deadcode**

   * 添加调用 `derive_asset_artifact_projection()` 和 `DeadcodeAnalyzer`，把 `dead_nodes` 写进 graph metadata 。
   * LinkageAnalyzer 可以先占位，后面再细化静/动态链接推断逻辑。

5. **阶段 2：逐步接通第三方依赖链路（按你的多 GraphManager 设计）**

   * 新增 `DependencyCollector` hook，收集 `DEPENDENCY_DISCOVERED` 到 `Transaction._discovered_deps`。
   * 实现 `dependency_resolver.resolve_dependencies()`，按生态调用 DepFetcher 拉取到缓存。
   * 在 `_link_dependency_graph()` 中创建 Proxy 节点 + 向 GraphRegistry / GlobalDAG 写边。

做完 1–4 之后，你就能：

* 用 `depanalyzer scan <本地 CMake/Hvigor 项目> -o out.json` 真正跑出一张图；
* 图中已经包含 CMake target/source/link + Hvigor module/依赖、Native bridge 契约，以及资产→产物投影和 deadcode 结果；
* 图文件会被写到 `.depanalyzer_cache/graphs/{graph_id}.json` 并登记进 GraphRegistry，可被后续的 export/query 使用。

第三方依赖、多图 Markov 风格传播和更完整的合规分析，可以在这个基础上按你 TODO 里的 Stage 2 / Stage 3 逐步推进。