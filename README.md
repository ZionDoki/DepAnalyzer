# Depanalyzer - 依赖关系分析工具

## ⚡ 快速开始

### 安装依赖

项目支持两种方式安装/运行：

1) 使用 Poetry（推荐）
```bash
curl -sSL https://install.python-poetry.org | python3 -  # 若尚未安装
poetry install
poetry run depanalyzer scan /path/to/project -o output.json
```

2) 使用已有虚拟环境 / 全局 Python（需提前安装依赖）
```bash
pip install .
# 或 pip install -e .
depanalyzer scan /path/to/project -o output.json
```

**许可证检测额外依赖**：如需启用 `--enable-license-check`，请确保系统已安装 `scancode` 可执行文件。

**版本要求**：Python >=3.12, <=3.14

### 基本用法

```bash
# 扫描项目并生成依赖图
poetry run depanalyzer scan /path/to/project -o graph.json

# 启用详细日志
poetry run depanalyzer -v scan /path/to/project -o graph.json

# 查看帮助
poetry run depanalyzer --help

# 仅生成回退扁平树（用于未知生态/许可证对比）
poetry run depanalyzer scan /path/to/project \
  --fallback-tree --no-analyze -o graph.json
```

## 📁 项目架构

项目采用模块化分层架构，按功能组织代码：

```
depanalyzer/
├── main.py                 # 主入口点
├── cli/                    # 命令行接口
│   └── scan.py            # 扫描命令实现
├── runtime/                # 运行时核心
│   ├── orchestrator.py    # 生命周期编排器
│   ├── phases/            # 生命周期阶段实现 (Acquire, Detect, Parse, etc.)
│   ├── dependency_resolver.py # 依赖解析
│   └── worker.py          # 并行工作进程管理
├── graph/                  # 图数据核心
│   ├── core/              # 图管理器与后端封装
│   └── models/            # 节点与边的数据模型 (NodeSpec, EdgeSpec)
├── parsers/                # 多语言解析器与依赖获取
│   ├── base.py            # 解析器与 Fetcher 基类
│   └── registry.py        # 插件注册表
├── analysis/               # 静态分析算法
│   └── deadcode.py        # 死代码检测等
└── utils/                  # 通用工具
```

## 🏗️ 架构层次说明

### 1. **命令行接口** (`cli/`)
- **职责**: 处理用户输入，解析参数，分发命令到对应的处理逻辑。
- **主要组件**: `scan.py` (扫描), `export.py` (导出), `dag.py` (DAG工具)。

### 2. **运行时核心** (`runtime/`)
- **职责**: 管理分析生命周期，协调各个阶段（Phase）的执行，管理并发任务和依赖解析。
- **主要组件**:
    - `orchestrator.py`: 驱动整个分析流程（Acquire -> Export）。
    - `phases/`: 各个生命周期阶段的具体实现。
    - `dependency_resolver.py`: 第三方依赖的下载与缓存管理。

### 3. **图数据核心** (`graph/`)
- **职责**: 提供统一的图数据结构，管理节点和边的增删查改，处理图的持久化和投影。
- **主要组件**:
    - `core/manager.py`: 单次事务的图管理器。
    - `models/schema.py`: 统一的节点（NodeSpec）和边（EdgeSpec）定义。
    - `ops/`: 图操作算法（如合并、投影、SCC压缩）。

### 4. **解析器层** (`parsers/`)
- **职责**: 实现特定生态（如 C/C++, Hvigor, Maven）的检测、解析和依赖获取逻辑。
- **主要组件**:
    - `registry.py`: 解析器与 Fetcher 的自动发现与注册。
    - `base.py`: 定义 Detector, Parser, DepFetcher 等标准接口。

### 5. **分析层** (`analysis/`)
- **职责**: 基于构建好的依赖图进行高级分析。
- **主要组件**: `deadcode.py` (死代码检测), `uncertainty.py` (不确定性分析)。

## 🚀 使用方法

```bash
# 运行依赖分析
python main.py --repo /path/to/repo --out output.json --workers 8 --max-depth 3 --max-deps 200
```

### 参数说明:
- `--repo`: 要分析的仓库根目录
- `--out`: 输出图文件(.json 或 .gml格式)
- `--workers`: 最大并行工作线程数(默认:8)
- `--max-depth`: 第三方依赖递归深度(默认:3)
- `--max-deps`: 全局最大第三方依赖数量上限（可选）

## 📊 核心特性

- **多语言支持**: 插件式解析器架构，易于扩展新语言
- **并发处理**: 多线程任务调度，充分利用系统资源
- **依赖管理**: 完整的第三方依赖解析链，支持缓存和递归
- **图形化输出**: 统一的图数据格式，支持JSON和GML导出
- **错误处理**: 全面的异常处理和降级策略

## 🔧 扩展新语言解析器

1. 在 `parsers/` 下创建新的语言文件夹
2. 实现 `code_parser.py` (必需) 和 `config_parser.py` (可选)
3. 继承 `BaseCodeParser` 和 `BaseConfigParser` 类
4. 重写解析方法，解析器会被自动发现和注册

## 📈 架构优势

- **清晰的职责分离**: 每个层次都有明确的职责边界
- **高度模块化**: 组件间耦合度低，易于维护和扩展
- **可扩展性**: 插件式架构支持快速添加新功能
- **并发安全**: 线程安全的设计支持高效并行处理






## 📦 安装与运行

### 1) 使用 pip 本地安装

```bash
pip install .
# 或开发模式安装
pip install -e .
```

安装完成后将自动生成 CLI：

```bash
# 依赖关系分析（基础）
depanalyzer --repo /path/to/repo --out output.json --workers 8 --max-depth 3 --max-deps 200

# 启用许可证检测与兼容性检查
depanalyzer \
  --repo /path/to/repo \
  --out output.json \
  --enable-license-check \
  --scancode-cmd scancode \
  --license-map-out license_map.json \
  --license-out compatible.json
```

环境与依赖说明：
- 需要 Python >=3.12, <=3.14
- 若启用 `--enable-license-check`，需系统可执行 `scancode`（建议安装 ScanCode 工具），并且会使用内置的 `liscopelens` 模块进行兼容性分析；此逻辑也会在第三方仓获取后对其源码进行扫描，并将结果缓存于对应缓存目录中。

### 2) 作为模块调用

```python
from depanalyzer.runtime.api import create_transaction

tx = create_transaction(source="/path/to/repo", max_workers=8, max_dependency_depth=3)
result = tx.execute()
if result.success:
    # Graph is managed internally, check TransactionResult for details
    print(f"Analysis completed: {result.node_count} nodes")
```

### 3) 常见问题
- 若 `depanalyzer` 命令不可用，请确认安装过程无报错，并确保 Python 的脚本目录在 `PATH` 中
- 许可证相关命令需要外部工具 `scancode` 可用
- 可通过 `depanalyzer --install` 下载并解压官方 `scancode-toolkit` 到固定路径（`~/.depanalyzer/scancode-toolkit/scancode`，Windows 下为 `scancode.bat`），无需写入系统 PATH；默认版本为 32.4.1，若需自定义版本可使用 `depanalyzer --install 32.4.1`
- 若需自定义下载链接（例如不同平台或版本），可设置环境变量 `SCANCODE_DOWNLOAD_URL`
- 安装脚本会自动匹配本机 Python 版本（支持 3.9/3.10/3.11/3.12/3.13，无法识别时默认下载 3.9 包）

### ScanCode 子命令用法

直接扫描目录（无需依赖 `scan` 输出，格式与原有模式一致）：

```bash
depanalyzer scancode --path /path/to/repo -o license_map.json
```

- 只需提供待检测目录，输出仍为 `{graph_node_id: spdx_license_expression}` 的 JSON 映射

复用 `scan` 产出的图缓存并保持命名空间对齐：

```bash
depanalyzer scancode --cache-dir .dep_cache --source /path/to/repo --third-party -o license_map.json
```

- `--cache-dir` 指向 `scan` 时使用的缓存目录（可直接给到其中的 `graphs/`），`--source` 用于解析 `<cache-dir>/<source_stem>/graphs`
- `--third-party` 会扫描缓存的三方依赖，`--force` 可强制重新调用 ScanCode 覆盖已存在的 `<graph_id>.licenses.json`
- 当使用 `--path` 直扫目录时，`--third-party` 参数会被忽略（因为没有依赖图可用）
