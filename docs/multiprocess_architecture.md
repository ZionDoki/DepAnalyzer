# 多进程 Transaction 并行执行架构

## 概述

本文档描述了 depanalyzer 的多进程并行执行架构，该架构通过进程池实现 Transaction 之间的真正并行，避免了 Python GIL 的限制。

## 架构设计

### 核心组件

#### 1. TransactionCoordinator（全局协调器）

**文件：** `depanalyzer/runtime/coordinator.py`

**功能：**
- 管理全局 `ProcessPoolExecutor`（固定进程池大小，默认为 CPU 核心数）
- 提供 `submit()` 接口将 Transaction 推入进程池
- 收集所有 Transaction 的执行结果
- 单例模式，确保全局唯一实例

**关键方法：**
```python
# 获取单例实例
coordinator = TransactionCoordinator.get_instance(max_processes=8)

# 提交 Transaction
future = coordinator.submit(transaction)

# 等待所有完成
results = coordinator.wait_all(timeout=3600)

# 关闭进程池
coordinator.shutdown()
```

#### 2. Transaction（支持序列化）

**文件：** `depanalyzer/runtime/transaction.py`

**改进：**
- 添加 `transaction_id` 唯一标识符（UUID）
- 实现 `__getstate__` / `__setstate__` 支持跨进程序列化
- Worker 和 EventBus 在子进程中重新创建（不序列化）
- 返回 `TransactionResult` 而不是 `GraphManager`

**序列化机制：**
```python
def __getstate__(self):
    # 移除不可序列化对象
    state = self.__dict__.copy()
    state["worker"] = None
    state["eventbus"] = None
    return state

def __setstate__(self, state):
    # 在子进程中重建
    self.__dict__.update(state)
    self.worker = Worker(max_workers=self.max_workers)
    self.eventbus = EventBus()
```

#### 3. GraphRegistry（多进程共享）

**文件：** `depanalyzer/graph/registry.py`

**改进：**
- 使用 `multiprocessing.Manager().dict()` 替代普通 dict
- 使用 `multiprocessing.Lock` 替代 `threading.RLock`
- 支持跨进程的图元数据共享
- 每个 Transaction 的完整图数据独立存储

**共享机制：**
```python
# 在主进程中初始化
_manager = multiprocessing.Manager()
_lock = _manager.Lock()
_registry = _manager.dict()

# 所有子进程可访问
with GraphRegistry._lock:
    registry[graph_id] = metadata
```

#### 4. Worker（保持线程池）

**文件：** `depanalyzer/runtime/worker.py`

**特点：**
- **Transaction 级别：** 使用进程池（避免 GIL）
- **Task 级别：** 使用线程池（适合 I/O 密集）
- 添加进程 ID 到所有日志输出

## 并行执行流程

### 1. 主 Transaction 提交

```
CLI (主进程)
  └─> TransactionCoordinator.submit(main_transaction)
      └─> ProcessPoolExecutor.submit(_run_transaction_worker)
          └─> 子进程 A 执行 main_transaction.run()
```

### 2. 依赖发现与子 Transaction 提交

```
子进程 A: main_transaction
  ├─> Phase D: _phase_resolve_deps()
  │   ├─> _discover_dependencies() → [dep1, dep2, dep3]
  │   ├─> coordinator.submit(child_tx_1) → 子进程 B
  │   ├─> coordinator.submit(child_tx_2) → 子进程 C
  │   └─> coordinator.submit(child_tx_3) → 子进程 D
  │
  └─> 等待所有子 Transaction 完成
      ├─> child_tx_1.result() → 成功
      ├─> child_tx_2.result() → 成功
      └─> child_tx_3.result() → 失败（记录错误）
```

### 3. 并行度控制

**进程池大小：** 固定（默认 `os.cpu_count()`）

**并发控制：**
- 最大并发 Transaction = 进程池大小
- 超出部分排队等待
- 每个进程内的 Task 使用线程池并行（默认 8 线程）

**总并发量：**
```
总并发 = 进程数 × 每进程线程数
例如：8 进程 × 8 线程 = 64 并发任务
```

## 关键设计决策

### 1. 为什么 Transaction 用进程？

**原因：**
- 完全避免 GIL 限制
- 每个 Transaction 可能解析不同的代码库，CPU 密集
- 进程隔离提供更好的错误容错

**代价：**
- 进程创建开销（通过进程池缓解）
- 内存占用更高（每个进程独立内存空间）
- 序列化开销（pickle Transaction 对象）

### 2. 为什么 Task 仍用线程？

**原因：**
- Task 主要是 I/O 密集（文件读取、tree-sitter 解析会释放 GIL）
- 线程切换开销小于进程
- 简化实现，避免过度复杂

### 3. 状态共享策略

**共享：**
- GraphRegistry（元数据）：`multiprocessing.Manager`

**不共享：**
- GraphManager（完整图数据）：每个 Transaction 独立
- Worker / EventBus：每个进程独立实例

**原因：**
- 避免大对象跨进程传输
- 减少锁竞争
- 提高并行度

## 使用示例

### CLI 使用

```bash
# 自动使用多进程执行
depanalyzer scan /path/to/project --workers 8 --max-depth 3
```

### 编程接口

```python
from depanalyzer.runtime.coordinator import TransactionCoordinator
from depanalyzer.runtime.transaction import Transaction

# 创建协调器
coordinator = TransactionCoordinator.get_instance(max_processes=4)

# 提交主 Transaction
main_tx = Transaction(
    source="/path/to/project",
    max_workers=8,
    max_dependency_depth=3
)

future = coordinator.submit(main_tx)

# 等待完成
result = future.result(timeout=3600)

if result.success:
    print(f"Completed: {result.node_count} nodes, {result.edge_count} edges")
else:
    print(f"Failed: {result.error}")

# 关闭
coordinator.shutdown()
```

## 性能预期

### GIL 影响消除

| 场景 | 旧架构（线程） | 新架构（进程） |
|-----|--------------|--------------|
| **单 Transaction** | 受 GIL 限制 | 完全消除 |
| **多 Transaction** | 串行执行 | 并行执行（最多 N 个进程） |
| **I/O 操作** | 可并行（释放 GIL） | 可并行 |
| **tree-sitter 解析** | 可并行（释放 GIL） | 可并行 |
| **纯 Python 图操作** | 串行（GIL 竞争） | 并行（进程隔离） |

### 预期加速比

- **单个大型项目：** 1-2x（取决于 CPU vs I/O 比例）
- **带依赖的项目：** 3-10x（依赖并行解析）
- **大规模批量分析：** 接近线性加速（N 个进程）

## 日志追踪

所有日志现在包含：
- **进程 ID：** `[PID 12345]`
- **Transaction ID：** `transaction_id` UUID

**示例日志：**
```
[PID 12345] Transaction abc-123 initialized for source: /project
[PID 12346] Starting transaction def-456 for: /dep1
[PID 12347] Starting transaction ghi-789 for: /dep2
[PID 12345] Waiting for 2 child transactions to complete
[PID 12346] Transaction def-456 completed in 15.3s
[PID 12347] Transaction ghi-789 completed in 12.8s
[PID 12345] Transaction abc-123 completed in 18.5s
```

## 调试建议

### 1. 启用详细日志

```python
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [PID %(process)d] %(name)s - %(levelname)s - %(message)s'
)
```

### 2. 序列化问题

如果遇到 pickle 错误：
- 确保 Transaction 参数可序列化（str, int, Path）
- 避免传递 lambda、局部函数、复杂对象
- 检查 `__getstate__` 是否正确排除不可序列化对象

### 3. 死锁调试

- 检查 GraphRegistry 锁持有时间
- 避免在持锁时执行长时间操作
- 使用 `timeout` 参数防止永久阻塞

### 4. 内存泄漏

- 每个进程独立内存空间
- 注意 Manager.dict() 的内存占用
- 定期清理已完成的 Transaction 结果

## 后续优化方向

1. **动态进程池大小：** 根据系统负载调整
2. **优先级队列：** Transaction 优先级调度
3. **分布式支持：** 跨机器的进程池
4. **事件总线跨进程：** 支持跨进程事件通知
5. **增量更新：** 避免重复分析未变更的依赖

## 技术限制

1. **Windows 支持：** 需要 `if __name__ == "__main__"` 保护
2. **序列化限制：** 不是所有对象都可 pickle
3. **内存开销：** 多进程比多线程占用更多内存
4. **调试难度：** 多进程 bug 比多线程更难追踪
5. **文件句柄限制：** 注意系统 ulimit

## 总结

新的多进程架构通过以下方式解决 GIL 瓶颈：

✅ **Transaction 级别进程隔离** - 完全消除 GIL 影响
✅ **Task 级别线程并行** - 保持 I/O 高效
✅ **全局进程池管理** - 控制资源占用
✅ **共享元数据，隔离数据** - 平衡共享与性能
✅ **完整日志追踪** - 简化调试

这个架构为大规模依赖分析提供了可扩展的并行执行能力。
