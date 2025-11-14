# 混编依赖图连接系统文档

## 概述

depanalyzer现在支持跨语言混编项目的依赖图连接，特别是**OHPM/Hvigor + CMake**混编场景。通过契约驱动架构，系统能够自动识别和连接不同构建系统产生的构建产物。

## 核心设计

### 1. 统一路径归一化

所有节点ID使用统一的路径格式：
- **内部路径**：`//relative/to/root_path`
- **外部路径**：`//../external/path`

示例：
```python
# 项目内文件
"//module/src/index.ts"
"//native/build/libxxx.so"

# 项目外依赖
"//../external_lib/.gitignore"
```

### 2. 构建接口契约（BuildInterfaceContract）

契约是连接混编依赖的核心抓手，包含：

```python
@dataclass
class BuildInterfaceContract:
    provider_artifact: str      # Provider产物路径（如 "//native/build/libxxx.so"）
    consumer_artifact: str      # Consumer期望路径（如 "//module/libs/arm64-v8a/libxxx.so"）
    artifact_name: str          # 产物名称（如 "libxxx.so"）
    contract_type: ContractType # NAPI | ARTIFACT_NAME | PATH | CONFIG
    confidence: float           # 匹配置信度 (0.0-1.0)
    evidence: List[str]         # 证据链
    interface_files: List[str]  # 接口文件（如 .d.ts）
    impl_files: List[str]       # 实现文件（如 .cpp）
```

### 3. 三层抓手优先级

系统使用多种策略匹配契约，按优先级排序：

| 抓手 | 置信度 | 匹配方式 | 状态 |
|------|--------|----------|------|
| **Contract** | 0.95 | NAPI函数名匹配 | 待实现 |
| **Artifact** | 0.85 | 产物名称匹配 | ✅ 已实现 |
| **Path** | 0.75 | 路径包含关系 | ✅ 已实现（HvigorCMakeBridge） |

## 工作流程

### 阶段1: PARSE - 契约注册

#### Hvigor侧（Consumer）

当HvigorParser解析`oh-package.json5`发现native依赖时：

```json
{
  "dependencies": {
    "mynativelib": "file:../native/types"
  }
}
```

自动注册consumer侧契约：

```python
registry.register(BuildInterfaceContract(
    provider_artifact="",  # 待匹配
    consumer_artifact="//module/libs/arm64-v8a/libmynativelib.so",
    artifact_name="libmynativelib.so",
    contract_type=ContractType.ARTIFACT_NAME,
    evidence=["hvigor_dependency:mynativelib", "interface://native/types/index.d.ts"]
))
```

**代码位置**：`depanalyzer/parsers/hvigor/config_parser.py:327-372`

#### CMake侧（Provider）

当CMakeParser解析`CMakeLists.txt`发现shared library时：

```cmake
add_library(mynativelib SHARED src/binding.cpp)
```

自动注册provider侧契约：

```python
registry.register(BuildInterfaceContract(
    provider_artifact="//native/build/libmynativelib.so",
    consumer_artifact="",  # 待匹配
    artifact_name="libmynativelib.so",
    contract_type=ContractType.ARTIFACT_NAME,
    evidence=["cmake_target:mynativelib", "src_path://native/CMakeLists.txt"]
))
```

**代码位置**：`depanalyzer/hooks/cmake_graph_builder.py:120-176`

### 阶段2: JOIN - 契约匹配

Transaction在JOIN阶段执行`ContractMatcher` Hook：

```python
def _phase_join(self):
    # 执行ContractMatcher
    matcher = ContractMatcher(self._graph_manager, eventbus)
    matcher.execute()
```

**匹配逻辑**（`depanalyzer/graph/contract_registry.py:182-213`）：

1. 获取所有consumer和provider契约
2. 按`artifact_name`匹配（如 `libmynativelib.so`）
3. 合并成完整契约，设置置信度=0.85
4. 返回匹配结果

**代码位置**：`depanalyzer/hooks/contract_matcher.py:44-111`

### 阶段3: 图边创建

ContractMatcher为每个匹配的契约创建依赖边：

```python
graph_manager.add_edge(
    source="//module/libs/arm64-v8a/libmynativelib.so",  # consumer
    target="//native/build/libmynativelib.so",           # provider
    edge_kind=EdgeKind.DEPENDS_ON,
    confidence=0.85,
    evidence=["hvigor_dependency:mynativelib", "cmake_target:mynativelib"],
    contract_type="artifact_name"
)
```

**最终图结构**：

```
[Hvigor Module: //module/oh-package.json5]
    ↓ CONTAINS
[Consumer Artifact: //module/libs/arm64-v8a/libmynativelib.so]
    ↓ DEPENDS_ON (conf=0.85, contract_type=artifact_name)
[Provider Artifact: //native/build/libmynativelib.so]
    ↓ PRODUCED_BY
[CMake Target: //native/CMakeLists.txt:mynativelib]
```

## 实际使用示例

### 场景：HarmonyOS混编应用

**项目结构**：
```
my_ohpm_app/
├── module/
│   ├── oh-package.json5      # 声明native依赖
│   ├── src/index.ts          # ArkTS代码
│   └── libs/                 # 期望的.so位置
│       └── arm64-v8a/
│           └── libmylib.so
└── native/
    ├── CMakeLists.txt        # 定义add_library(mylib SHARED ...)
    ├── src/
    │   └── binding.cpp       # NAPI实现
    └── types/
        ├── oh-package.json5  # types字段指向index.d.ts
        └── index.d.ts        # TypeScript声明
```

**运行分析**：
```bash
python -m depanalyzer.main scan my_ohpm_app
```

**输出日志**：
```
[INFO] HvigorParser: Found native bridge: mylib -> //native/types/index.d.ts
[DEBUG] HvigorParser: Registered consumer contract: mylib (abi=arm64-v8a)
[DEBUG] CMakeGraphBuilder: Registered provider contract: mylib (artifact=//native/build/libmylib.so)
[INFO] ContractMatcher: Matched 1 complete contracts
[INFO] ContractMatcher: Created 1 cross-language dependency edges
```

**依赖边**：
```
Consumer: //module/libs/arm64-v8a/libmylib.so
  → Provider: //native/build/libmylib.so
  Confidence: 0.85
  Evidence: ["hvigor_dependency:mylib", "cmake_target:mylib"]
```

## 扩展增强

### 待实现功能

#### 1. Contract抓手（NAPI函数名匹配）

**目标**：通过解析.d.ts和.cpp文件实现更高置信度匹配

**.d.ts解析器**（`parsers/hvigor/dts_parser.py`）：
```python
def parse_dts_exports(dts_file: Path) -> List[str]:
    """提取export function声明"""
    # 使用tree-sitter-typescript解析
    # 返回：["myFunction", "anotherFunction"]
```

**NAPI提取器**（`parsers/cpp/napi_extractor.py`）：
```python
def extract_napi_functions(cpp_file: Path) -> List[str]:
    """提取napi_create_function调用"""
    # 正则匹配：napi_create_function(env, "xxx", ...)
    # 返回：["myFunction", "anotherFunction"]
```

**匹配逻辑**（`graph/contract_registry.py`）：
```python
dts_funcs = parse_dts_exports(consumer.interface_files[0])
napi_funcs = extract_napi_functions(provider.impl_files[0])
match_ratio = len(set(dts_funcs) & set(napi_funcs)) / max(len(dts_funcs), 1)
if match_ratio > 0.7:
    contract.confidence = 0.95
```

#### 2. Config抓手（ABI/架构一致性验证）

**验证点**：
- CMake: `CMAKE_SYSTEM_PROCESSOR`, `CMAKE_ANDROID_ARCH_ABI`
- Hvigor: `build-profile.json5`的ABI配置

**匹配逻辑**：
```python
if cmake_abi == hvigor_abi:
    contract.confidence *= 1.1  # 提升置信度
else:
    logger.warning("ABI mismatch: CMake=%s, Hvigor=%s", cmake_abi, hvigor_abi)
```

## 架构优势

### 1. 解析顺序无关

**传统方法**：Parser A必须在Parser B之前运行 → 顺序耦合
**契约方法**：所有Parser独立注册契约 → JOIN阶段统一匹配

### 2. 证据可追溯

每条依赖边都带有完整的证据链：
```python
edge.evidence = [
    "hvigor_dependency:mylib",
    "native_dir://native",
    "interface://native/types/index.d.ts",
    "cmake_target:mylib",
    "src_path://native/CMakeLists.txt"
]
```

### 3. 多抓手融合

同一对节点可能有多个匹配证据：
- Artifact抓手: 产物名一致 → 0.85
- Path抓手: 路径包含 → 0.75
- Contract抓手: 函数名匹配 → 0.95

**融合策略**：取最高confidence，合并evidence

### 4. 扩展性强

**新增Rust支持**只需：
1. 实现`RustParser`注册provider契约（`cdylib`）
2. ContractMatcher自动匹配 → 无需修改核心逻辑

## API参考

### GraphManager扩展

```python
# 路径归一化
node_id = graph_manager.normalize_path("/path/to/file.cpp")
# => "//relative/to/root/file.cpp"

# 便捷方法（自动归一化）
graph_manager.add_normalized_node(
    path="/path/to/file.cpp",
    node_type=NodeType.CODE
)

graph_manager.add_normalized_edge(
    source_path="/path/to/source.cpp",
    target_path="/path/to/header.h",
    edge_kind=EdgeKind.INCLUDE
)
```

### ContractRegistry

```python
from depanalyzer.graph.contract_registry import ContractRegistry

registry = ContractRegistry()  # 单例

# 注册契约
registry.register(contract)

# 查询
providers = registry.get_providers(artifact_name="libxxx.so")
consumers = registry.get_consumers(artifact_name="libxxx.so")

# 统计
stats = registry.get_statistics()
# => {"total": 10, "complete": 5, "provider_only": 3, "consumer_only": 2}

# 匹配
matched = registry.match_contracts()
# => [BuildInterfaceContract(...), ...]
```

## 调试技巧

### 1. 查看契约注册日志

```bash
export LOG_LEVEL=DEBUG
python -m depanalyzer.main scan my_project 2>&1 | grep "contract"
```

输出：
```
[DEBUG] HvigorParser: Registered consumer contract: mylib (abi=arm64-v8a)
[DEBUG] CMakeGraphBuilder: Registered provider contract: mylib (artifact=//native/build/libmylib.so)
```

### 2. 检查契约统计

在JOIN阶段查看：
```
[INFO] ContractMatcher: Contract registry stats: {'total': 8, 'complete': 0, 'provider_only': 4, 'consumer_only': 4, 'by_type': {'artifact_name': 8}}
[INFO] ContractMatcher: Matched 4 complete contracts
```

### 3. 验证依赖边

查看生成的图中的DEPENDS_ON边：
```python
edges = graph_manager.edges(edge_kind=EdgeKind.DEPENDS_ON)
for source, target, key, attrs in edges:
    print(f"{source} -> {target}")
    print(f"  Confidence: {attrs['confidence']}")
    print(f"  Evidence: {attrs['evidence']}")
```

## 常见问题

### Q: 为什么我的native依赖没有被连接？

**检查清单**：
1. Hvigor配置是否正确声明了`file:`依赖？
2. CMake是否创建了`SHARED` library？
3. 产物名称是否一致？（consumer期望`libxxx.so`，provider也产生`libxxx.so`）
4. 查看DEBUG日志确认契约是否注册

### Q: 多个ABI如何处理？

系统为每个ABI注册独立契约：
```
Consumer: //module/libs/arm64-v8a/libxxx.so
Consumer: //module/libs/armeabi-v7a/libxxx.so
Consumer: //module/libs/x86_64/libxxx.so
```

JOIN阶段会自动匹配所有可能的组合。

### Q: 如何自定义匹配逻辑？

**方法1**：扩展`ContractRegistry.match_contracts()`
**方法2**：创建新的Hook订阅契约事件
**方法3**：修改Parser注册契约时的metadata

## 总结

混编依赖图连接系统通过**契约驱动架构**实现了：

✅ **统一路径格式**：所有节点ID归一化
✅ **解析顺序无关**：延迟匹配，无耦合
✅ **多抓手融合**：Artifact/Path/Contract多重验证
✅ **双层连接**：产物级+文件级依赖
✅ **证据可追溯**：完整的匹配证据链
✅ **易于扩展**：新增语言/构建系统成本低

当前已支持**Hvigor+CMake**混编的基本Artifact匹配（置信度0.85），后续可通过实现Contract抓手（NAPI函数名匹配）提升至0.95。
