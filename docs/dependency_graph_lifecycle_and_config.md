# 依赖构件图生命周期与插件化配置架构说明

本文档说明当前 `depanalyzer` 中“依赖构件图”的完整生命周期，以及如何以**插件化方式**为不同语言生态（ecosystem）维护和注册它们自己的配置。

目标是：

- 生命周期清晰：Acquire → Detect → Parse → ResolveDeps → Join → Analyze → Export  
- 配置可扩展：每个生态独立维护自己的配置结构，但通过统一入口注册，供 Transaction 生命周期消费。

> 说明：本文描述的是**目标架构**。核心生命周期和部分配置（Hvigor/CPP）已经落地在代码里，其它插件化配置注册的扩展可以按本文档逐步补齐。

---

## 1. 生命周期总览

中心实体是 `Transaction`（`depanalyzer/runtime/transaction.py`），生命周期如下：

1. **ACQUIRE**：源获取
   - 入口：`Transaction._phase_acquire()`
   - 职责：
     - 使用 `Workspace` 处理本地路径或 Git URL，得到只读 `root_path`。
     - 在首次运行时生成 `graph_id`。

2. **DETECT**：目标检测
   - 入口：`_phase_detect()`
   - 职责：
     - 从 `EcosystemRegistry` 获取所有已注册生态的 `Detector` 类。
     - 为每个生态构造一个 `BaseDetector` 子类实例，并发起 `detect()`。
     - 将每个生态发现的配置文件（CMakeLists/oh-package.json5 等）记录在 `_detected_targets` 中。
   - 配置注入：
     - 每个 Detector 可以接收一个 per-ecosystem 的 detect-config。

3. **PARSE**：解析阶段（两级）

   3.1 **Config Parsing（配置解析）**
   - 入口：`_phase_parse()` 中 Stage 1
   - 职责：
     - 对每个生态 + 目标文件创建 `BaseParser` 子类实例。
     - Parser：
       - 从配置文件提取事实（targets、modules、deps 等）。
       - 调用 `GraphManager` 创建节点/边。
       - 通过 `EventBus` 发布结构化事件（例如 CMake events）。
       - 注册跨生态契约（BuildInterfaceContract）。
       - 可选：实现 `discover_code_files()` 返回待解析的源码文件列表。
   - 配置注入：
     - 每个 Parser 接收 per-ecosystem 的 parser-config。

   3.2 **Code Parsing（代码解析）**
   - 入口：`_phase_parse()` 中 Stage 2
   - 职责：
     - 聚合所有 parser 返回的源码文件列表，按生态分组。
     - 使用全局 `CodeParserPool`（ProcessPoolExecutor）调用各生态的 `BaseCodeParser.parse_file()`。
     - 将 `parse_file()` 返回的 includes/imports 信息通过 `_process_code_parse_result()` 落到图里：
       - 确保每个 source file 有一个规范化的 `NodeType.CODE` 节点（ID 通常为 `//relative/path`）。
       - 针对不同生态生成合适的 include/import 边：
         - CPP：利用 CMake target 的 `include_dirs` 精确解析 header。
         - Hvigor：将 ArkTS/TS/JS import 解析为 `IMPORT` 边，目标节点同样使用规范 ID。
   - 配置注入：
     - `BaseCodeParser` 可以接收 per-ecosystem 的 code-parser-config，例如 ArkTS import 向上查找深度。

4. **RESOLVE_DEPS**：第三方依赖解析
   - 入口：`_phase_resolve_deps()`
   - 职责：
     - 从 `DependencyCollector` 收集由 Parser 发出的 `DependencySpec`。
     - 以及扫描图中的 `EXTERNAL_LIBRARY/EXTERNAL_DEP` 节点构造额外的 `DependencySpec`。
     - 使用 `dependency_resolver.resolve_dependencies()` 选择合适的 `DepFetcher`（按 ecosystem），拉取依赖到本地 cache。
     - 为每个成功的依赖创建一个子 `Transaction`，递归执行生命周期。

5. **JOIN**：跨域/跨生态 Linking
   - 入口：`_phase_join()`
   - 职责：
     - 运行每个生态的 `BaseLinker`：
       - CppLinker：丰富 `link_libraries` 边，推断 `linkage_type`（shared/static）。
       - HvigorLinker：模块 ↔ code membership、打包 HAP/HAR/HSP 节点、Hvigor ↔ CMake path bridge、Hvigor shared_library → C++ 子图链接。
     - 运行全局的 `GlobalContractLinker`：
       - 从 `ContractRegistry` 读取并匹配 BuildInterfaceContract，生成消费者/提供者 artifact 之间的 `DEPENDS_ON` 边，以及接口/实现文件之间的 `IMPLEMENTS_NATIVE` 边。
   - 配置注入：
     - 每个 Linker 接收 per-ecosystem linker-config（例如是否启用 path bridge）。
     - Contract 匹配通过 ContractMatchConfig 控制启用哪些抓手（artifact_name/NAPI/EXTERN_C/CONFIG/PATH 等）。

6. **ANALYZE**：图分析
   - 入口：`_phase_analyze()`
   - 职责：
     - 资产→构件投影：`GraphManager.derive_asset_artifact_projection()`，从 SOURCES/CONTAINS/INCLUDE/link_libraries 等关系推导 PART_OF/AFFECTS 边。
     - Deadcode：从 HAP/HAR/HSP/EXECUTABLE 作为 root 做反向可达，标记 unreachable 节点。
     - Linkage：分析 link edges，推断 static/dynamic/module 信息，并写入 graph metadata。
   - 配置注入：
     - 投影部分使用 ProjectionConfig 控制 fallback 行为、include 占位解析等。

7. **EXPORT**：导出/缓存
   - 入口：`_phase_export()` + `_flush_graph_to_disk()`
   - 职责：
     - 以简单 JSON（原生 NetworkX node_link 格式）导出当前 Transaction 的图。
     - 使用 `GraphRegistry` 记录 graph 的 cache 路径与 summary。
     - 使用 `GlobalDAG` 记录父子 graph 的依赖关系。

---

## 2. 配置模型与插件化思路

### 2.1 顶层配置：GraphBuildConfig

统一的生命周期配置对象定义在 `depanalyzer/runtime/graph_config.py` 中：

```python
from dataclasses import dataclass, field
from depanalyzer.graph.projection_config import ProjectionConfig

@dataclass
class GraphBuildConfig:
    detect: DetectConfig = field(default_factory=DetectConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    code_parser: CodeParserConfig = field(default_factory=CodeParserConfig)
    linker: LinkerConfig = field(default_factory=LinkerConfig)
    projection: ProjectionConfig = field(default_factory=ProjectionConfig)
    contract_match: ContractMatchConfig = field(default_factory=ContractMatchConfig)

    @classmethod
    def default(cls) -> "GraphBuildConfig":
        return cls()
```

`Transaction` 在构造时接收一个 `graph_build_config`（可选），没传则使用 `GraphBuildConfig.default()`：

```python
from depanalyzer.runtime.graph_config import GraphBuildConfig

tx = Transaction(
    source="/path/to/repo",
    max_workers=8,
    max_dependency_depth=3,
    graph_build_config=GraphBuildConfig.default(),
)
```

内部保存为 `self._graph_build_config`，并在各个 `_phase_*` 中按需取子配置。

### 2.2 现有的 per-ecosystem 配置切片（Hvigor/CPP）

现在已经落地的 Hvigor/CPP 配置是静态挂在 GraphBuildConfig 下的，例如：

- DetectConfig：`detect.hvigor`, `detect.cpp`
- ParserConfig：`parser.hvigor`, `parser.cpp`
- LinkerConfig：`linker.hvigor`, `linker.cpp`

并通过统一的 `for_ecosystem(ecosystem: str)` 提供给各组件：

```python
detect_cfg = graph_build_config.detect.for_ecosystem("hvigor")
parser_cfg = graph_build_config.parser.for_ecosystem("cpp")
linker_cfg = graph_build_config.linker.for_ecosystem("hvigor")
```

### 2.3 完全插件化：由 parser 各自维护配置并统一注册

为了实现“完全插件化、不同语言生态灵活配置，由 parser 各自维护，统一注册”，推荐的架构是：

1. **每个生态自己定义配置 dataclass**  
   - 放在各自的 parser 包内部，例如：
     - `depanalyzer/parsers/hvigor/config_model.py`
     - `depanalyzer/parsers/cpp/config_model.py`
   - 由生态 owner 自己维护字段（detect/parser/code_parser/linker 所需的全部配置）。

2. **每个生态提供一个 EcosystemConfig 包装对象**  
   - 用于集中存放该生态的所有配置（Detect/Parser/CodeParser/Linker 一起），例如：

   ```python
   # depanalyzer/parsers/hvigor/config_model.py
   from dataclasses import dataclass

   @dataclass
   class HvigorDetectConfig:
       include_root_dirs: list[str] | None = None
       ignore_node_modules: bool = True

   @dataclass
   class HvigorParserConfig:
       enable_native_dependencies: bool = True
       infer_missing_modules: bool = True

   @dataclass
   class HvigorCodeParserConfig:
       max_import_ancestor_levels: int = 8

   @dataclass
   class HvigorLinkerConfig:
       attach_code_without_module: bool = False
       auto_infer_entry_module: bool = False
       enable_path_bridge: bool = True
       enable_shared_library_cpp_link: bool = True

   @dataclass
   class HvigorEcosystemConfig:
       detect: HvigorDetectConfig = HvigorDetectConfig()
       parser: HvigorParserConfig = HvigorParserConfig()
       code_parser: HvigorCodeParserConfig = HvigorCodeParserConfig()
       linker: HvigorLinkerConfig = HvigorLinkerConfig()
   ```

3. **生态在初始化时注册自己的配置工厂（已落地）**  

   现在的实现是一个真正的 `EcosystemConfigRegistry`，定义在  
   `depanalyzer/runtime/ecosystem_config_registry.py`：

   ```python
   class EcosystemConfigRegistry:
       _factories: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

       @classmethod
       def register_config_factory(
           cls,
           ecosystem: str,
           factory: Callable[[Dict[str, Any]], Any],
       ) -> None:
           cls._factories[ecosystem] = factory

       @classmethod
       def from_dict(cls, ecosystem: str, data: Dict[str, Any]) -> Optional[Any]:
           factory = cls._factories.get(ecosystem)
           if factory is None:
               return None
           return factory(data or {})
   ```

   Hvigor 在 `depanalyzer/parsers/__init__.py` 中注册自己的组件和配置工厂（Cpp 生态同理）：

   ```python
   from depanalyzer.parsers.registry import register_ecosystem
   from depanalyzer.parsers.hvigor.config_model import HvigorEcosystemConfig
   from depanalyzer.parsers.hvigor.detector import HvigorDetector
   from depanalyzer.parsers.hvigor.config_parser import HvigorParser
   from depanalyzer.parsers.hvigor.dep_fetcher import HvigorDepFetcher
   from depanalyzer.parsers.hvigor.code_parser import HvigorCodeParser
   from depanalyzer.parsers.hvigor.linker import HvigorLinker
   from depanalyzer.runtime.ecosystem_config_registry import EcosystemConfigRegistry

   register_ecosystem(
       ecosystem="hvigor",
       detector_class=HvigorDetector,
       parser_class=HvigorParser,
       fetcher_class=HvigorDepFetcher,
       code_parser_class=HvigorCodeParser,
       linker_class=HvigorLinker,
   )

   EcosystemConfigRegistry.register_config_factory(
       "hvigor",
       HvigorEcosystemConfig.from_dict,
   )
   ```

4. **GraphBuildConfig.from_dict 与 per-ecosystem 配置（已落地）**

   当前的 `GraphBuildConfig` 在 `depanalyzer/runtime/graph_config.py` 中已经包含：

   ```python
   @dataclass
   class GraphBuildConfig:
       detect: DetectConfig = field(default_factory=DetectConfig)
       parser: ParserConfig = field(default_factory=ParserConfig)
       code_parser: CodeParserConfig = field(default_factory=CodeParserConfig)
       linker: LinkerConfig = field(default_factory=LinkerConfig)
       projection: ProjectionConfig = field(default_factory=ProjectionConfig)
       contract_match: ContractMatchConfig = field(default_factory=ContractMatchConfig)

       # 插件化入口：ecosystem -> ecosystem_config
       ecosystem_configs: Dict[str, Any] = field(default_factory=dict)

       @classmethod
       def from_dict(cls, data: Dict[str, Any]) -> "GraphBuildConfig":
           cfg = cls.default()

           # 1) 语言无关配置
           proj = data.get("projection", {}) or {}
           ...
           cfg.projection = ProjectionConfig(...从 proj 取字段...)

           cm = data.get("contract_match", {}) or {}
           cfg.contract_match = ContractMatchConfig(...从 cm 取字段...)

           # 2) 生态配置：读取 [ecosystems.<eco>.*] 并交给 EcosystemConfigRegistry
           ecosystems_dict = data.get("ecosystems", {}) or {}
           if isinstance(ecosystems_dict, dict):
               for eco, eco_cfg_data in ecosystems_dict.items():
                   if not isinstance(eco_cfg_data, dict):
                       continue
                   eco_cfg = EcosystemConfigRegistry.from_dict(eco, eco_cfg_data)
                   if eco_cfg is not None:
                       cfg.ecosystem_configs[eco] = eco_cfg

           return cfg
   ```

   对于 Hvigor/CPP，`GraphBuildConfig` 里仍保留了静态切片（`detect.hvigor` 等），但当 TOML/JSON 中提供了
   `[ecosystems.hvigor.*]` / `[ecosystems.cpp.*]` 时，会优先使用 `ecosystem_configs` 中的配置。

5. **各阶段从 GraphBuildConfig 中切片（已落地）**

   为了屏蔽内部结构，`GraphBuildConfig` 暴露了一组按生态取切片的 helper：

   ```python
   def get_detect_config(self, ecosystem: str) -> Optional[Any]: ...
   def get_parser_config(self, ecosystem: str) -> Optional[Any]: ...
   def get_code_parser_config(self, ecosystem: str) -> Optional[Any]: ...
   def get_linker_config(self, ecosystem: str) -> Optional[Any]: ...
   ```

   实现上，它会：

   - 若存在 `ecosystem_configs[eco]` 且包含对应属性（detect/parser/code_parser/linker），优先返回该属性。
   - 否则回退到静态切片：`detect.for_ecosystem(eco)` 等。

   Transaction 在各生命周期阶段统一这么使用：

   ```python
   # Detect 阶段
   detect_cfg = self._graph_build_config.get_detect_config(eco)
   detector = det_cls(self.workspace.root_path, eventbus, config=detect_cfg)

   # Parser 阶段
   parser_cfg = self._graph_build_config.get_parser_config(eco)
   parser = p_cls(..., config=parser_cfg)

   # CodeParser 阶段（通过 CodeParserPool 注入）
   code_cfg = self._graph_build_config.get_code_parser_config(eco)
   batch_futures = code_pool.submit_batch(file_paths, ecosystem=eco, config=code_cfg)

   # Linker 阶段
   linker_cfg = self._graph_build_config.get_linker_config(eco)
   linker = linker_cls(self._graph_manager, config=linker_cfg)
   ```

   这样就可以做到：

   - 生命周期入口统一从 `GraphBuildConfig` 取配置切片；  
   - 每个生态用自己的 `*EcosystemConfig.from_dict()` 解析 TOML/JSON；  
   - Hvigor/CPP 等已有生态在没有外部配置时仍可使用静态默认值。

---

## 3. 配置示例（TOML / JSON）

下面给出几个完整的配置示例，帮助理解如何在实际项目中使用，并说明当前已经支持哪些配置。

> 配置加载入口：`depanalyzer.runtime.config_loader.load_graph_build_config(source)`  
> - `source` 可以是：
>   - `None`：使用内置默认配置；
>   - `dict`：已经解析好的配置映射；
>   - 字符串或 `Path`：既可以是 `config.toml` / `config.json` 路径，也可以是 **内联的 TOML/JSON 字符串**。
>
> CLI 中通过 `depanalyzer scan ... -c/--config` 传入，scan 命令内部会调用 `load_graph_build_config`。

### 3.1 `config.toml` 示例（Hvigor + CMake 混编项目）

假设我们有一个 OHPM/Hvigor + CMake 混编的 HarmonyOS 项目，希望：

- Hvigor 只扫描 `entry/` 与 `feature/` 模块；  
- Hvigor 忽略 `node_modules`；  
- CMake 忽略 `build/` 与 `third_party/`；  
- Hvigor 解析 `native_dependencies`，并允许自动为无 module.json 的目录推断模块；  
- ArkTS import 解析时，最多向上搜索 6 层目录；  
- Hvigor ↔ CMake 的 path bridge 关闭，只通过契约连接；  
- Projection 阶段关闭所有 fallback（仅使用严格 PART_OF/CONTAINS）。

可以编写如下 `config.toml`：

```toml
# 资产 -> 构件投影行为
[projection]
enable_fallback = false
enable_link_closure = false
enable_header_closure = false

# 契约匹配开关
[contract_match]
enable_artifact_name = true
enable_napi = false
enable_extern_c = false
enable_path = false
enable_config = false

# -------------------------
# Hvigor 生态配置
# -------------------------

[ecosystems.hvigor.detect]
include_root_dirs = ["entry", "feature"]
ignore_node_modules = true

[ecosystems.hvigor.parser]
enable_native_dependencies = true
infer_missing_modules = true

[ecosystems.hvigor.code_parser]
# ArkTS import 向上查找的最大层数（默认 8）
max_import_ancestor_levels = 6

[ecosystems.hvigor.linker]
attach_code_without_module = false
auto_infer_entry_module = false
enable_path_bridge = false
enable_shared_library_cpp_link = true

# -------------------------
# CMake / C++ 生态配置
# -------------------------

[ecosystems.cpp.detect]
ignore_build_dirs = true
ignore_third_party_dirs = true

[ecosystems.cpp.parser]
ignore_object_library_targets = false
ignore_interface_library_targets = false

[ecosystems.cpp.code_parser]
# 目前只是占位开关，可为将来的 C++ 代码解析实验特性预留
enable_experimental_features = false

[ecosystems.cpp.linker]
enable_linkage_enrichment = true
```

使用方式：

```bash
depanalyzer scan . -o graph.json -c config.toml
```

如果不传 `-c`，则等价于使用 `GraphBuildConfig.default()`：  

- `projection` / `contract_match` 使用各自 dataclass 的默认值；  
- Hvigor / Cpp 使用 `HvigorDetectConfig` / `CMakeDetectConfig` 等的默认值；  
- 可以针对只关心的少数字段在 TOML 里“按需覆盖”。

### 3.2 `config.json` / 内联字符串示例

同样的配置可以写成 JSON（结构与 TOML 一致）并从文件或字符串加载，例如：

```json
{
  "projection": {
    "enable_fallback": false,
    "enable_link_closure": false,
    "enable_header_closure": false
  },
  "contract_match": {
    "enable_artifact_name": true,
    "enable_napi": false,
    "enable_extern_c": false,
    "enable_path": false,
    "enable_config": false
  },
  "ecosystems": {
    "hvigor": {
      "detect": {
        "include_root_dirs": ["entry", "feature"],
        "ignore_node_modules": true
      },
      "parser": {
        "enable_native_dependencies": true,
        "infer_missing_modules": true
      },
      "code_parser": {
        "max_import_ancestor_levels": 6
      },
      "linker": {
        "attach_code_without_module": false,
        "auto_infer_entry_module": false,
        "enable_path_bridge": false,
        "enable_shared_library_cpp_link": true
      }
    },
    "cpp": {
      "detect": {
        "ignore_build_dirs": true,
        "ignore_third_party_dirs": true
      },
      "parser": {
        "ignore_object_library_targets": false,
        "ignore_interface_library_targets": false
      },
      "code_parser": {
        "enable_experimental_features": false
      },
      "linker": {
        "enable_linkage_enrichment": true
      }
    }
  }
}
```

使用方式：

```bash
# 从 JSON 文件加载
depanalyzer scan . -o graph.json -c config.json

# 直接传入 JSON 字符串
depanalyzer scan . -o graph.json -c '{"projection": {"enable_fallback": false}}'

# 直接传入 TOML 字符串（首字符不是 { / [ 时会按 TOML 解析）
depanalyzer scan . -o graph.json -c '
    [projection]
    enable_fallback = false
'
```

`load_graph_build_config` 内部会自动：

- 判断字符串是否是一个存在的文件路径；  
- 根据扩展名（.toml/.tml/.json）或内容首字符来选择 TOML / JSON 解析器；  
- 将解析出的 dict 交给 `GraphBuildConfig.from_dict`。

### 3.3 新增一个 Python 生态的插件化配置示例（设计草案）

假设你要新增一个 Python 生态（`ecosystem="python"`），推荐做法：

1. 在 `depanalyzer/parsers/python/config_model.py`：

```python
from dataclasses import dataclass

@dataclass
class PythonDetectConfig:
    include_patterns: list[str] = ("**/pyproject.toml", "**/setup.cfg")
    ignore_venv: bool = True

@dataclass
class PythonParserConfig:
    resolve_extras: bool = True

@dataclass
class PythonCodeParserConfig:
    max_import_depth: int = 8

@dataclass
class PythonLinkerConfig:
    link_wheels_as_artifacts: bool = True

@dataclass
class PythonEcosystemConfig:
    detect: PythonDetectConfig = PythonDetectConfig()
    parser: PythonParserConfig = PythonParserConfig()
    code_parser: PythonCodeParserConfig = PythonCodeParserConfig()
    linker: PythonLinkerConfig = PythonLinkerConfig()
```

2. 在 `depanalyzer/parsers/python/__init__.py` 注册：

```python
from depanalyzer.parsers.registry import register_ecosystem
from depanalyzer.runtime.ecosystem_config_registry import EcosystemConfigRegistry
from depanalyzer.parsers.python.detector import PythonDetector
from depanalyzer.parsers.python.config_parser import PythonParser
from depanalyzer.parsers.python.code_parser import PythonCodeParser
from depanalyzer.parsers.python.dep_fetcher import PythonDepFetcher
from depanalyzer.parsers.python.linker import PythonLinker
from depanalyzer.parsers.python.config_model import PythonEcosystemConfig

register_ecosystem(
    ecosystem="python",
    detector_class=PythonDetector,
    parser_class=PythonParser,
    fetcher_class=PythonDepFetcher,
    code_parser_class=PythonCodeParser,
    linker_class=PythonLinker,
)

# 这里注册的工厂签名为 (data: dict) -> PythonEcosystemConfig
EcosystemConfigRegistry.register_config_factory(
    "python",
    PythonEcosystemConfig.from_dict,
)
```

用户可以在 `config.toml` 中提供：

```toml
[ecosystems.python.detect]
include_patterns = ["**/pyproject.toml", "**/setup.cfg"]
ignore_venv = true
```

`GraphBuildConfig.from_dict` 会从 `[ecosystems.python.*]` 读取字典并通过  
`EcosystemConfigRegistry.from_dict("python", data)` 交给 `PythonEcosystemConfig.from_dict`，
最后在 Transaction 的 Detect/Parse/Join/CodeParse 阶段通过 `get_*_config("python")` 注入。

---

## 4. 开发说明：如何为一个生态实现完整的“生命周期 + 配置 + 注册”

以 Hvigor 为例，总结一个生态所需的文件与步骤。

1. **定义配置模型（由生态自己维护）**

   - 位置：`depanalyzer/parsers/hvigor/config_model.py`
   - 结构：`HvigorEcosystemConfig(detect/parser/code_parser/linker)`

2. **实现 Detector / Parser / CodeParser / DepFetcher / Linker**

   - Detector：继承 `BaseDetector`，读取 `self.config`（HvigorDetectConfig）。
   - Parser：继承 `BaseParser`，读取 `self.config`（HvigorParserConfig）。
   - CodeParser：继承 `BaseCodeParser`，可在 `__init__(config)` 中消费 code-parser-config，并在 `parse_file` 中只使用不可变配置字段。
   - DepFetcher：继承 `BaseDepFetcher`，不直接使用生命周期配置（通常只用 DependencySpec）。
   - Linker：继承 `BaseLinker`，读取 `self.config`（HvigorLinkerConfig），在 `link()` 中按开关启用/禁用不同 linking 逻辑。

3. **统一注册生态组件与配置**

   - 在 `depanalyzer/parsers/hvigor/__init__.py` 中：
     - 使用 `register_ecosystem` 注册 Detector/Parser/DepFetcher/CodeParser/Linker。
     - 使用 `EcosystemConfigRegistry.register_config_factory` 注册 `HvigorEcosystemConfig` 的工厂函数。

4. **Transaction 生命周期自动消费配置**

   - Detect 阶段：`BaseDetector` 子类会收到 `eco_cfg.detect`。
   - Parse 阶段：`BaseParser` 子类会收到 `eco_cfg.parser`。
   - Code Parsing 阶段：`BaseCodeParser` 子类可通过 CodeParserPool + eco_cfg.code_parser 获得配置（需要在 CodeParserPool 中做一次性注入）。
   - Join 阶段：`BaseLinker` 子类会收到 `eco_cfg.linker`。

5. **版本演进策略**

   - 对于已经内置在核心 GraphBuildConfig 中的生态（例如 Hvigor/CPP），可以先保持现有静态字段，逐步迁移到插件化配置工厂的方式。
   - 新生态（例如 Python/NPM）建议直接采用插件化配置注册模式，避免修改核心 `GraphBuildConfig` 源码。

---

## 5. 小结

- 生命周期方面，`Transaction` 已经清晰地分为 ACQUIRE → DETECT → PARSE → RESOLVE_DEPS → JOIN → ANALYZE → EXPORT，各阶段的职责明确、接口稳定。
- 配置方面，`GraphBuildConfig` 统一管理生命周期相关配置，并通过 per-ecosystem 切片（`get_detect_config` / `get_parser_config` / `get_code_parser_config` / `get_linker_config`）注入 Detector/Parser/CodeParser/Linker，再配合 `projection` 与 `contract_match`。
- 为了实现“完全插件化、由 parser 各自维护配置并统一注册”，推荐每个生态：
  1. 自己定义一个 `*EcosystemConfig`（detect/parser/code_parser/linker），并实现 `from_dict`；
  2. 在 `parsers/__init__.py` 中通过 `EcosystemConfigRegistry.register_config_factory("<eco>", <EcosystemConfig>.from_dict)` 注册配置工厂；
  3. 用户在 TOML/JSON 中通过 `[ecosystems.<eco>.*]` 提供配置，`GraphBuildConfig.from_dict` + `get_*_config("<eco>")` 会在生命周期各阶段自动传入对应组件。

按照本文档的模式，未来新增任何语言/工具链生态，只需在 `parsers/<ecosystem>/` 下实现一组标准组件 + 一个配置模型 + 一个注册入口，就可以无缝融入统一的“依赖构件图生命周期 + 配置框架”中。  
