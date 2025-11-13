# 生态化 Parser 架构设计

## 概述

depanalyzer 采用**生态化设计**，每个语言/工具链生态（Ecosystem）必须实现三个核心接口：

1. **Detector** - 轻量级扫描，识别目标文件
2. **Parser** - 详细解析，构建依赖图
3. **DepFetcher** - 依赖拉取，下载/克隆依赖

这种设计确保：
- ✅ 每个生态独立管理自己的依赖逻辑
- ✅ 跨生态依赖通过注册表自动路由
- ✅ 新生态可独立添加，无需修改核心代码

## 架构组件

### 1. 基类接口 (`parsers/base.py`)

#### BaseDetector

```python
class BaseDetector(ABC):
    NAME: str = "base"
    ECOSYSTEM: str = "base"  # 生态标识符

    def __init__(self, workspace_root: Path, eventbus: EventBus):
        ...

    @abstractmethod
    def detect(self) -> List[Path]:
        """扫描工作区，返回目标文件列表"""
        ...
```

**职责：**
- 快速扫描工作区，识别配置文件（如 CMakeLists.txt, package.json）
- 发布检测事件到 EventBus
- **不解析**文件内容，只识别位置

#### BaseParser

```python
class BaseParser(ABC):
    NAME: str = "base"
    ECOSYSTEM: str = "base"

    def __init__(self, workspace_root: Path, graph_manager: GraphManager, eventbus: EventBus):
        ...

    @abstractmethod
    def parse(self, target_path: Path) -> None:
        """解析目标文件，构建图节点和边"""
        ...
```

**职责：**
- 详细解析目标文件（AST 分析、配置解析）
- 创建图节点（源文件、模块、包等）
- 创建图边（依赖关系、调用关系）
- 发现并记录外部依赖（供 DepFetcher 处理）

#### BaseDepFetcher（新增）

```python
class BaseDepFetcher(ABC):
    NAME: str = "base"
    ECOSYSTEM: str = "base"

    def __init__(self, cache_root: Path):
        ...

    @abstractmethod
    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """拉取依赖到本地缓存"""
        ...

    @abstractmethod
    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """判断是否可以处理该依赖"""
        ...

    # 通用工具方法
    def git_clone(self, url, target_dir, branch=None, tag=None, depth=1):
        """Git 克隆（基类提供）"""
        ...

    def download_file(self, url, target_path):
        """下载文件（基类提供）"""
        ...

    def extract_archive(self, archive_path, target_dir):
        """解压归档（基类提供）"""
        ...
```

**职责：**
- 下载/克隆依赖到本地缓存
- 处理生态特定的包管理器（如 npm, pip, OHPM）
- 复用基类的 Git 克隆等通用方法
- 返回依赖的本地路径供后续 Transaction 分析

### 2. 生态注册表 (`parsers/registry.py`)

```python
class EcosystemRegistry:
    """全局注册表，管理所有生态的组件"""

    def register_ecosystem(
        self,
        ecosystem: str,
        detector_class: Type[BaseDetector],
        parser_class: Type[BaseParser],
        fetcher_class: Type[BaseDepFetcher],
    ):
        """注册完整生态"""
        ...

    def get_detector(self, ecosystem: str) -> Type[BaseDetector]:
        ...

    def get_parser(self, ecosystem: str) -> Type[BaseParser]:
        ...

    def get_dep_fetcher(self, ecosystem: str) -> Type[BaseDepFetcher]:
        ...
```

**使用示例：**
```python
from depanalyzer.parsers.registry import register_ecosystem
from depanalyzer.parsers.cpp.detector import CppDetector
from depanalyzer.parsers.cpp.parser import CppParser
from depanalyzer.parsers.cpp.dep_fetcher import CppDepFetcher

# 注册 C++ 生态
register_ecosystem(
    ecosystem="cpp",
    detector_class=CppDetector,
    parser_class=CppParser,
    fetcher_class=CppDepFetcher,
)
```

## 目录结构

```
depanalyzer/parsers/
├── base.py              # 基类接口（Detector, Parser, DepFetcher）
├── registry.py          # 生态注册表
│
├── cpp/                 # C/C++ 生态
│   ├── __init__.py
│   ├── detector.py      # CMake/配置文件检测
│   ├── parser.py        # CMake/代码解析（调用 cmake/ 和 code_parser.py）
│   ├── dep_fetcher.py   # Git-based 依赖拉取
│   ├── cmake/           # CMake 专用解析器
│   └── code_parser.py   # C++ 代码解析
│
├── hvigor/              # HarmonyOS 生态
│   ├── __init__.py
│   ├── detector.py      # hvigorfile.ts 检测
│   ├── parser.py        # hvigorfile 和 TS 代码解析
│   ├── dep_fetcher.py   # OHPM/Git 依赖拉取
│   ├── config_parser.py
│   └── code_parser.py
│
└── npm/                 # Node.js 生态（未来）
    ├── __init__.py
    ├── detector.py      # package.json 检测
    ├── parser.py        # package.json 和 JS 代码解析
    └── dep_fetcher.py   # npm/yarn 依赖拉取
```

## 依赖解析流程

### 场景：C++ 项目依赖 HarmonyOS 库

```
1. [主 Transaction] 分析 C++ 项目
   └─> CppDetector 发现 CMakeLists.txt
   └─> CppParser 解析 CMakeLists.txt
       └─> 发现外部依赖: {"name": "harmony-sdk", "ecosystem": "hvigor", "url": "git@..."}

2. [Phase D: Resolve Deps]
   └─> 查询 EcosystemRegistry，获取 hvigor 的 DepFetcher
   └─> HvigorDepFetcher.fetch(dep_spec)
       └─> git_clone() 拉取到缓存
       └─> 返回本地路径

3. [创建子 Transaction] 分析 HarmonyOS 依赖
   └─> HvigorDetector 发现 hvigorfile.ts
   └─> HvigorParser 解析 hvigorfile.ts
       └─> 发现更多依赖...

4. [递归解析] 直到达到 max_dependency_depth
```

### 跨生态依赖路由

```python
# 在 Transaction._phase_resolve_deps() 中
for dep in discovered_dependencies:
    ecosystem = dep.get("ecosystem", "unknown")

    # 从注册表获取对应生态的 DepFetcher
    fetcher_class = registry.get_dep_fetcher(ecosystem)

    if fetcher_class is None:
        logger.warning(f"No fetcher for ecosystem: {ecosystem}")
        continue

    # 实例化并拉取
    fetcher = fetcher_class(cache_root)
    local_path = fetcher.fetch(dep_spec)

    if local_path:
        # 创建子 Transaction 分析该依赖
        child_tx = Transaction(source=str(local_path), ...)
        coordinator.submit(child_tx)
```

## 示例实现：C++ DepFetcher

**文件：** `depanalyzer/parsers/cpp/dep_fetcher.py`

```python
class CppDepFetcher(BaseDepFetcher):
    NAME = "cpp_dep_fetcher"
    ECOSYSTEM = "cpp"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        return dep_spec.ecosystem == "cpp" or \
               ".git" in (dep_spec.source_url or "")

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        cache_dir = self.get_cache_dir(dep_spec)

        # 使用基类的 git_clone 方法
        success = self.git_clone(
            url=dep_spec.source_url,
            target_dir=cache_dir,
            tag=dep_spec.version,
            depth=1,
        )

        return cache_dir if success else None
```

## 示例实现：Hvigor DepFetcher

**文件：** `depanalyzer/parsers/hvigor/dep_fetcher.py`

```python
class HvigorDepFetcher(BaseDepFetcher):
    NAME = "hvigor_dep_fetcher"
    ECOSYSTEM = "hvigor"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        return dep_spec.ecosystem in ("hvigor", "harmonyos", "ohpm")

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        dep_type = dep_spec.metadata.get("type", "ohpm")

        if dep_type == "git":
            # Git 依赖：使用基类方法
            return self._fetch_from_git(dep_spec)
        elif dep_type == "ohpm":
            # OHPM 依赖：使用 ohpm CLI 或直接下载
            return self._fetch_from_ohpm(dep_spec)
        elif dep_type == "local":
            # 本地依赖：直接返回路径
            return Path(dep_spec.metadata["local_path"])

    def _fetch_from_ohpm(self, dep_spec):
        # 1. 尝试 ohpm CLI
        if self._try_ohpm_cli_install(...):
            return cache_dir

        # 2. 回退到直接下载 tarball
        tarball_url = f"https://ohpm.openharmony.cn/..."
        self.download_file(tarball_url, tarball_path)
        self.extract_archive(tarball_path, cache_dir)
        return cache_dir
```

## 添加新生态的步骤

### 1. 创建生态目录

```bash
mkdir -p depanalyzer/parsers/python
touch depanalyzer/parsers/python/__init__.py
```

### 2. 实现三个接口

```python
# detector.py
class PythonDetector(BaseDetector):
    NAME = "python_detector"
    ECOSYSTEM = "python"

    def detect(self) -> List[Path]:
        # 查找 requirements.txt, setup.py, pyproject.toml
        return self.workspace_root.rglob("requirements.txt")

# parser.py
class PythonParser(BaseParser):
    NAME = "python_parser"
    ECOSYSTEM = "python"

    def parse(self, target_path: Path) -> None:
        # 解析 requirements.txt
        # 创建 Package 节点
        # 记录依赖关系

# dep_fetcher.py
class PythonDepFetcher(BaseDepFetcher):
    NAME = "python_dep_fetcher"
    ECOSYSTEM = "python"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        return dep_spec.ecosystem == "python"

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        # 使用 pip download 或从 PyPI 下载
        ...
```

### 3. 注册生态

```python
# parsers/python/__init__.py
from depanalyzer.parsers.registry import register_ecosystem
from .detector import PythonDetector
from .parser import PythonParser
from .dep_fetcher import PythonDepFetcher

def register():
    register_ecosystem(
        ecosystem="python",
        detector_class=PythonDetector,
        parser_class=PythonParser,
        fetcher_class=PythonDepFetcher,
    )

# 在应用启动时调用
register()
```

## 设计优势

### ✅ 独立性
- 每个生态完全独立，可单独开发和测试
- 添加新生态不影响现有生态

### ✅ 可扩展性
- 新生态只需实现三个接口
- 注册即可使用，无需修改核心代码

### ✅ 复用性
- Git 克隆、文件下载、归档解压等通用逻辑在基类实现
- 子类直接调用，避免重复代码

### ✅ 跨生态支持
- 通过注册表自动路由跨生态依赖
- C++ 项目依赖 Python 包？自动调用 PythonDepFetcher

### ✅ 多进程友好
- DepFetcher 设计为无状态，可在多进程中安全使用
- 缓存目录按生态/名称/版本隔离，避免冲突

## 与 Transaction 的集成

```python
# runtime/transaction.py
def _phase_resolve_deps(self):
    # 1. 收集当前图中的所有外部依赖
    dependencies = self._collect_external_dependencies()

    # 2. 按生态分组
    deps_by_ecosystem = {}
    for dep_spec in dependencies:
        ecosystem = dep_spec.ecosystem
        deps_by_ecosystem.setdefault(ecosystem, []).append(dep_spec)

    # 3. 为每个生态创建 DepFetcher 并拉取
    from depanalyzer.parsers.registry import EcosystemRegistry
    registry = EcosystemRegistry.get_instance()

    for ecosystem, deps in deps_by_ecosystem.items():
        fetcher_class = registry.get_dep_fetcher(ecosystem)
        if not fetcher_class:
            logger.warning(f"No fetcher for ecosystem: {ecosystem}")
            continue

        fetcher = fetcher_class(cache_root=self.cache_root)

        for dep_spec in deps:
            if not fetcher.can_handle(dep_spec):
                continue

            # 拉取依赖
            local_path = fetcher.fetch(dep_spec)

            if local_path:
                # 创建子 Transaction 分析
                child_tx = Transaction(
                    source=str(local_path),
                    parent_transaction_id=self.transaction_id,
                    max_dependency_depth=self.max_dependency_depth - 1,
                )
                coordinator.submit(child_tx)
```

## 总结

生态化 Parser 架构通过三接口设计（Detector, Parser, DepFetcher）+ 注册表机制，实现了：

1. **生态独立** - 每个生态自包含，互不干扰
2. **自动路由** - 跨生态依赖自动找到正确的 DepFetcher
3. **通用工具** - Git/下载/解压等通用逻辑由基类提供
4. **易于扩展** - 新生态只需三个文件 + 一次注册

这种设计使 depanalyzer 能够轻松支持多种语言和工具链的依赖分析。
