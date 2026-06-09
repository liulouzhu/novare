# Docker 沙箱安全漏洞报告：executor.py 被绕过

## 漏洞概述

**严重程度**: 高  
**影响范围**: 所有 `code_execute` 工具调用  
**发现日期**: 2026-06-09  
**修复日期**: 2026-06-09

`DockerSandboxManager.execute()` 在执行用户代码时，直接调用 `python /tmp/_run.py`，完全绕过了 Docker 容器内设计好的受限执行器 `executor.py`，导致所有应用层安全限制失效。

---

## 问题详情

### 问题代码（修复前）

**`web/backend/sandbox/manager.py` 第 99-102 行：**

```python
exit_code, (stdout, stderr) = container.exec_run(
    cmd=["python", "-u", "/tmp/_run.py"],   # ❌ 直接用原始 Python 执行
    demux=True,
)
```

### 执行流程对比

```
设计流程（正确）:
  用户代码 → put_archive(/tmp/_run.py) → executor.py 读取 → AST 检查 → 受限执行
                                          ↑ 有 import/builtin/path/timeout/输出 限制

实际流程（漏洞）:
  用户代码 → put_archive(/tmp/_run.py) → python /tmp/_run.py
                                          ↑ 无任何应用层限制
```

`executor.py` 是 Dockerfile 的 `CMD` 启动的进程，始终在容器内运行，但 manager.py 通过 `exec_run` 启动了另一个独立的 Python 进程，完全绕过了它。

---

## 被绕过的 6 层安全限制

| 保护机制 | executor.py 设计 | 实际执行路径 | 风险 |
|---|---|---|---|
| 禁止危险模块 (`os`, `subprocess`, `socket` 等) | ✅ AST 静态检查 | ❌ 无限制 | 可枚举文件系统、尝试本地连接 |
| 禁用危险内置函数 (`exec`, `eval`, `open`) | ✅ 从 builtins 删除 | ❌ 全部可用 | 可执行任意代码、读写文件 |
| 文件路径限制 (`/data/`, `/output/`) | ✅ `restricted_open()` | ❌ 无限制 | 可读取容器内任意文件 |
| 代码大小限制 (50KB) | ✅ stdin 读取后检查 | ❌ 无检查 | 可提交超大代码 |
| 超时强制 (60s) | ✅ `thread.join(timeout)` | ❌ 参数未传递 | 代码可无限运行 |
| 输出大小限制 (1MB) | ✅ 截断输出 | ❌ 无限制 | 可产生超大输出耗尽资源 |

---

## Docker 层面仍然有效的保护

容器创建时配置了以下 Docker 级别限制，这些作为纵深防御仍然有效：

- `mem_limit="512m"` — 内存上限
- `cpus=1.0` — CPU 限制
- `pids_limit=100` — 防止 fork 炸弹
- `network_disabled=True` — 无网络访问
- `read_only=True` — 只读根文件系统
- `cap_drop=["ALL"]` — 丢弃所有 Linux 能力
- `security_opt=["no-new-privileges"]` — 禁止提权
- `user="1000:1000"` — 非 root 用户

但这些不能替代应用层限制。攻击者仍可：
- `import os; os.listdir("/")` 枚举容器文件系统
- `import socket` 尝试本地 socket 连接
- 无超时地运行 `while True: pass` 占用 CPU
- 通过 stdout 泄露 `/data/` 下的数据

---

## 修复方案

### 修改的文件

| 文件 | 修改内容 |
|---|---|
| `docker/sandbox/executor.py` | 支持环境变量 `TIMEOUT_SECONDS`；支持从文件路径参数读取代码 |
| `web/backend/sandbox/manager.py` | 通过 `executor.py` 执行用户代码，传递超时环境变量，异步化 |

### 修复后的执行流程

```
用户代码
  ↓
put_archive → /tmp/_run.py（容器内临时文件）
  ↓
exec_run: python -u /executor.py /tmp/_run.py
  ↓
executor.py:
  1. 从 /tmp/_run.py 读取代码
  2. 检查代码大小 ≤ 50KB
  3. AST 静态检查：禁止 os/subprocess/socket 等模块
  4. 替换 builtins：删除 exec/eval/compile/__import__/open
  5. 替换 open()：只允许访问 /data/ 和 /output/
  6. 子线程执行，thread.join(timeout=TIMEOUT_SECONDS) 超时保护
  7. 输出截断至 1MB
  ↓
执行完毕后清理 /tmp/_run.py
```

### executor.py 关键变更

**1. 支持环境变量配置超时（第 14 行）：**

```python
# 修复前
TIMEOUT_SECONDS = 60

# 修复后
TIMEOUT_SECONDS = int(os.environ.get("TIMEOUT_SECONDS", 60))
```

**2. 支持从文件路径读取代码（第 37-48 行）：**

```python
def main():
    # 优先从文件路径读取（调用方传入），否则从 stdin 读取（向后兼容）
    if len(sys.argv) > 1:
        code_path = sys.argv[1]
        try:
            with open(code_path) as f:
                code = f.read()
        except FileNotFoundError:
            print(f"Error: code file not found: {code_path}", file=sys.stderr)
            sys.exit(1)
    else:
        code = sys.stdin.read()
```

**3. 修复 builtins_open 全局变量（第 15 行）：**

```python
# 修复前：运行时动态设置全局变量，存在竞态风险
global builtins_open
builtins_open = builtins.open

# 修复后：模块加载时捕获，确定性初始化
_original_open = builtins.open  # captured at import time
```

### manager.py 关键变更

**核心执行逻辑（第 103-114 行）：**

```python
# 修复前：直接用原始 Python 执行，绕过所有限制
exit_code, (stdout, stderr) = container.exec_run(
    cmd=["python", "-u", "/tmp/_run.py"],
    demux=True,
)

# 修复后：通过 executor.py 执行，传递超时环境变量
effective_timeout = max(1, min(timeout, 300))
exit_code, (stdout, stderr) = await asyncio.wait_for(
    asyncio.to_thread(
        container.exec_run,
        cmd=["python", "-u", "/executor.py", "/tmp/_run.py"],
        demux=True,
        environment={"TIMEOUT_SECONDS": str(effective_timeout)},
    ),
    timeout=effective_timeout + 5,  # 给 executor 5s 余量
)
```

**新增的保护措施：**

| 措施 | 说明 |
|---|---|
| 代码大小前置检查 | manager 层在写入容器前检查 `len(code) > 50KB` |
| 空代码快速返回 | 空代码直接返回，不创建容器 |
| 双层超时 | executor.py 线程超时 + manager asyncio 超时 |
| 临时文件清理 | 执行完毕后 `rm -f /tmp/_run.py` |
| 异步化 | `put_archive` 和 `exec_run` 通过 `asyncio.to_thread` 包装 |

---

## 修复前后对比

| 维度 | 修复前 | 修复后 |
|---|---|---|
| 执行入口 | `python /tmp/_run.py` | `python /executor.py /tmp/_run.py` |
| 模块限制 | ❌ 无 | ✅ AST 静态检查 |
| 内置函数限制 | ❌ 无 | ✅ exec/eval/open 等被删除 |
| 文件路径限制 | ❌ 无 | ✅ 只允许 /data/, /output/ |
| 代码大小限制 | ❌ 无 | ✅ 50KB（双层检查） |
| 超时强制 | ❌ 参数被忽略 | ✅ 双层保护（线程 + asyncio） |
| 输出大小限制 | ❌ 无 | ✅ 1MB 截断 |
| 临时文件清理 | ❌ 不清理 | ✅ 执行后清理 |

---

## 验证建议

修复后建议进行以下测试：

1. **正常代码执行**：`print("hello")` → 应正常返回
2. **模块限制**：`import os` → 应返回 `Module 'os' is not allowed`
3. **内置函数限制**：`exec("print(1)")` → 应返回 `name 'exec' is not defined`
4. **文件路径限制**：`open("/etc/passwd")` → 应返回 `open() denied`
5. **超时**：`while True: pass` → 应在超时后返回错误
6. **代码大小**：提交 >50KB 代码 → 应在 manager 层被拒绝
7. **输出大小**：产生大量输出 → 应被截断至 1MB
