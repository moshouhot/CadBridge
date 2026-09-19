# P1 审查整改记录（第二轮）：安全 harness 与测试有效性

**时间**：2026-09-18
**触发**：第二轮独立审查指出 T01-5 不得判 PASS，且安全 harness 本身未通过审查。
**结论**：已按审查逐项整改；**T01-5 仍记 NOT_RUN**；**在安全审查再次通过前暂停新的真机 CAD 实验**。

---

## 1. 审查指出的问题与处置

### 1.1 `tools/safe_process.py` 不能证明**归属**（最严重）

| # | 缺陷 | 处置 |
|---|---|---|
| 1 | `launch_and_record()` 丢弃 `Popen` 句柄，随后按安装目录**扫描并取 `found[0]`**，可能**认领用户进程** | 只承认 `Popen` 返回的 PID；**不再**按目录选进程。若该 PID 取不到身份则**返回 None（fail-closed）**，不认领其它进程 |
| 2 | `verify_owned()` 对创建时间/路径做**条件比较**，元数据缺失时仍通过，使调用方的 `{"pid": pid}` 回退可授权终止 | 身份字段**全部必需**；缺失或为空即**拒绝** |
| 3 | 对**已存在**进程「先记录再终止」只证明身份、不证明归属 | 记录带 **launch nonce**；`terminate_owned()` 要求该 nonce，手工拼装的记录一律拒绝 |
| 4 | 仅凭 PID 终止存在 **PID 复用竞态** | 优先用**保留的句柄**终止；PID 兜底前重新做完整身份校验 |
| 5 | 自检未覆盖既存实例/并发启动/元数据缺失/PID 复用/枚举失败/歧义交接 | 进程表改为**可注入**，上述场景全部用**伪造数据**测试（`--self-test` 不启动任何进程） |

自检现为 **18 项全通过**，含：
`a preexisting user instance is never adopted`、`PID reuse (creation time differs) is refused`、
`process-enumeration failure is refused`、`terminate_owned refuses a hand-assembled record`、
`concurrent starts each verify with their own identity`、`crossed identities are refused`。

### 1.2 目录扫描取 PID 的同类缺陷（attach 侧）

脚本虽已用 `safe_process` 启动 CAD，但随后**又按目录扫描取 `found[0]` 作为 attach 目标**。
这是同一「认领」缺陷，后果是把调试器**附加到错误的 CAD**。
已全部改为 `pid = owned.pid`（来自启动句柄），并在 `tools/selftest.sh` 加入静态检查防止回归。

### 1.3 测试有效性缺陷

| 缺陷 | 处置 |
|---|---|
| `t01-5-definitive.py` **硬编码** `"ACHIEVABLE"` 判定 | 改为**由检查结果派生**，并输出未主张项清单 |
| 断言检查**不存在**的 `queued` 字段 | 改为检查「值是否真的出现」 |
| 陈旧性用「错误串 vs 值」比较 | 改为**受保护表达式 + 已知基线**；**并撤回**「该测量已运行」的声明（见 §2） |
| 失败时仍 `return 0` | 有失败即**非零退出** |
| `finally` 中 `raise SystemExit` | **会掩盖在飞异常**。改为仅在 `sys.exc_info()[0] is None` 时决定退出码；已加入静态检查 |
| `poll_for` 按**累积字符串去重**消费输出 | 改为 `OutputCursor`：按**事件序号游标**消费、**重组被拆分块**、不做去重 |
| 未关联请求与结果 | 每次请求生成**新 nonce**，插件回传该 nonce，按 nonce 判定归属 |
| 几何比较用**子串**（`r=3` 也会匹配 `r=30`） | 插件改为输出结构化 `key=value`；测试**精确比较**数值 |
| 未断言「仍在同一停止点」 | 新增：读取期间**无** `continued`/`terminated`/`exited` 事件；读后**重新查询帧**确认同一文件同一行 |
| 未要求必需检查**全部执行** | 新增 `MANDATORY` 清单；缺失即判失败，避免「提前退出但仍绿」 |
| 管道掩盖退出码 | 验证时不再用 `| tail`/`| grep` 判定，改用 `$?`/`PIPESTATUS` |

### 1.4 COM 与线程

| 问题 | 处置 |
|---|---|
| `win32com.client.Dispatch` 回退可能启动/选中非预期实例 | **移除**；仅 `GetActiveObject`，失败即 `ComUnavailable` |
| 使用被遗弃的 COM 线程 | **未完成**：仍需改为**有界工作进程**。相关 COM 探针**暂停使用** |
| 缺少 HWND→自有 PID 校验 | 已提供 `com_verify_pid()`；**但尚未在所有 COM 探针中强制使用** |

## 2. 主动撤回的声明（审查指出，经复核成立）

| 原声明 | 事实 | 现表述 |
|---|---|---|
| 「受保护表达式 `-1→1` 已用于陈旧性反证」 | 该改动是在那次运行**之后**写入脚本的，**从未实际运行** | **撤回**；脚本已具备该逻辑，但**尚无运行证据** |
| 「`hover` 拒绝用户函数」→ 推广为「所有上下文」/「唯一可能的插件通道」 | 只测了**该函数**在 **`hover`** 下的行为 | 限定为该函数在该上下文；`repl` 已被证明可调用用户函数 |
| 「`repl` 的 `result` 为空」是普遍保证 | 是本机该宿主的**观测行为** | 记为观测，不作普遍保证 |
| 「未造成实际损害」/「约 8 次终止」/「安全已修复」 | 历史进程归属与影响**无法事后确证**；harness 也**尚未通过审查** | **撤回**具体次数与「无害」表述；仅记录所用命令不当与已采取的整改，**不声称已修复** |
| 「COM 超时 ⇒ 所有插件路径都不可能」 | COM 超时不能证明这一点；且**插件路径已证明可行** | 限定为「COM 路径在暂停期未返回」这一关联观测 |

## 3. 当前阻塞

**`BLOCKED_SAFETY_REVIEW`**：`safe_process` 归属证明已按要求重写并通过 18 项自检，
但审查意见还要求：

1. COM 探针改用**有界工作进程**实现，并**强制** `HWND→自有 PID` 校验；
2. `LiveReadLispFunction.cs` 的执行上下文/线程身份需对照 Autodesk **受支持回调文档**核实；
3. 增加**空闲正对照**与**经插件**的前/后置观测；
4. 环境独占性与既有环境阻塞需与项目所有者对齐。

在上述完成并**再次通过审查**前，**不开展新的真机 CAD 实验**。

## 4. 插件侧已完成的加固（`src/Plugin.Shared/LiveReadLispFunction.cs`）

审查要求「在下次测试前先给插件加仪表」。已完成，返回值现为结构化 `key=value`：

| 字段 | 用途 |
|---|---|
| `nonce` | 关联单次请求 |
| `thread_id` / `managed_tid` | 回调实际运行线程 |
| `depth` | 重入检测 |
| `in_command` | 命令上下文 |
| `doc` | 文档身份（检出读错文档） |
| `lock_ms` / `tx_ms` / `read_ms` / `total_ms` | 停顿可见，而非推断 |
| `entities` / `circles` | 结构化几何，避免子串误判 |
| `last_circle_handle` / `last_circle_center` / `last_circle_radius` | 精确几何 |
| `disposed` | 锁与事务是否已释放（**只读：无 Commit，析构即中止**） |
| `status` / `error_type` / `error` | 失败也作为**数据**返回，便于诊断 |

两个 Shell 均 **0 错误**编译；输出**不含任何 Autodesk 程序集**。

## 5. 已加入 `tools/selftest.sh` 的静态检查（防回归）

- 工具脚本中**禁止**按进程名终止（`Stop-Process`、`taskkill /IM`）；
- **禁止**未经 `safe_process` 归属校验的裸 `taskkill`；
- **禁止** `win32com.client.Dispatch` 回退；
- **禁止** `finally` 中**未加 `sys.exc_info()` 保护**的 `SystemExit`；
- **禁止**目录扫描取 PID（`found[0]`）。

以上均已用**负向注入**验证过确实会失败（非空检查）。

## 6. 受影响的证据与文件

- `docs/evidence/P1/20260917T083402Z/status.json`：T01-3/T01-5 → `NOT_RUN`；
  两个过程性条目 → `N/A`；新增 `SAFETY-REVIEW-REQUIRED` → `BLOCKED`。
  （早前误用 `PARTIAL`/`FIXED`，`make-manifest.py` 已正确拒绝，现校验通过。）
- `docs/evidence/P1/20260917T083402Z/T01-5/report.md`：结论收窄，声明撤回。
- `docs/evidence/P1/20260917T083402Z/T01-3/report.md`：过强表述降级，归因限度标注。
- `tools/safe_process.py`、`tools/selftest.sh`、`tools/t01-5-*.py`、`tools/dap-attach-*.py`
- `src/Plugin.Shared/LiveReadLispFunction.cs`

---

# 第三轮整改（第二轮审查之后）

第二轮审查判定「安全审查仍未通过」，并指出一批**具体缺陷**。已逐条核实（全部属实）并修复。

## 1. `safe_process.py` 的归属模型（重写）

| 审查指出的缺陷 | 核实 | 处置 |
|---|---|---|
| 任意非空 `launch_nonce` 即可授权匹配记录；无私有注册表；自检竟接受伪造的 `"abc"` | ✅ 属实（自检测的是**错误契约**） | 改为**模块私有注册表**：`launch_and_record()` 返回**不透明 token**，`terminate_owned()` **只接受注册表内的 token**；**dict 记录一律不接受** |
| `record()` 用 `dataclasses.asdict()`，会先深拷贝 `Popen` 再删除 `_handle`，遇到内部锁会抛异常 | ✅ 实测 `TypeError: cannot pickle '_thread.lock' object` | 改为显式**仅标量**投影 `audit()`；**不**使用 `asdict` |
| `record()` 去掉句柄，导致所有真实调用方都退回竞态的 `taskkill /PID` | ✅ 属实 | 终止**只走保留句柄**；**移除 PID 兜底**——句柄不可用即**拒绝终止**并如实报告 |
| 未校验「实际可执行文件」与「请求的可执行文件」一致 | ✅ 属实 | 新增 `requested_exe` 校验；不一致即拒绝 |
| 自检里 `set_provider(_PROVIDER)` 把假 provider 又装了回去 | ✅ 属实 | 改为 `provider_scope()` 上下文管理器，**异常时也**正确还原 |
| 缺少真实 launch/register/terminate 集成测试 | ✅ 属实 | 新增注入式用例：伪造 token、序列化、PID 复用、元数据缺失、**终止失败不回退**、无句柄不终止、既存实例不被认领 |

自检由 18 项增至 **28 项，全部通过**。

## 2. 现场实验脚本已实际停用

新增 `require_safety_review_passed()` 闸门，**默认拒绝**并**不启动任何 CAD**：

- 6 个 COM 现场脚本（`t01-5-*.py`）全部被拦截；
- 证据：`tooling/safety-gate-refusal.txt`（含真实报错文本）；
- 仅当人工显式设置 `CBRIDGE_ACK_UNREVIEWED_LIVE=1` 时才放行，且会打印警告。

## 3. `dap-probe.py` 新增本地接收游标

审查指出：适配器自带的 `seq` **不是**可靠的本地游标（跨会话可重复，也不表示已消费多少）。
已在 `DapClient` 增加单调递增的 `recv_index`，`OutputCursor` 改用该字段消费输出。

## 4. 探针修复

| 缺陷 | 处置 |
|---|---|
| `parse_fields()` 用了 `re` 但**未导入** | 已 `import re` |
| 硬编码 handle `2CE` | 改为与**夹具实际创建**的 handle 比较（由 continue 后的独立 COM 交叉验证取得）；取不到时只断言 handle 存在 |
| `SystemExit` 位于 `finally` | 已改为仅在 `sys.exc_info()[0] is None` 时决定退出码（**并加入静态检查防回归**） |
| 调用方自行拼装归属记录 | 全部迁移为 token API；`selftest.sh` 增加「禁止调用方构造归属记录」检查 |

## 5. 文档与记忆中的过强表述（已撤回）

| 原声明 | 现表述 |
|---|---|
| `repl` 是**唯一**可能的插件通道 | 本机**唯一已验证可行**的形态；`watch`/`clipboard` 对用户函数**未测** |
| `hover`/`watch`/`clipboard` 都拒绝用户函数 | 只测了**该函数**在 **`hover`** 下；其余未测 |
| `repl` 的 `result` **恒空** | **本机观测**，非普遍保证 |
| COM 超时 ⇒ 插件 CommandMethod 不可能 | **关联观测**；**不**证明所有插件路径都不可能（插件路径已可行） |
| attach 失败**原因已证明** | 三项**同时**改动，**各自必要性未单独证明** |
| 「安全已修复」 | **撤回**；harness 尚未通过审查 |
| 受保护表达式 `-1→1` 已用于陈旧性反证 | **撤回**：该改动在运行后写入，**从未运行** |

## 6. 仍未完成（阻塞下次审查）

1. COM 探针改**有界工作进程**，并**强制** `HWND→自有 PID` 校验后再访问图面。
2. `LiveReadLispFunction.cs` 的执行上下文/线程身份对照 Autodesk **受支持文档**核实；
   并**在访问数据库之前**拒绝非预期线程/嵌套进入（现仅在返回后报告，不足）。
3. `disposed=true` 需**如实反映每次释放结果**（当前释放抛异常时仍置 true）。
4. 补**空闲命令上下文线程基线**与**经插件**的前/后置观测。
5. 与项目所有者对齐**环境独占性**与既有环境阻塞。

**在上述完成并再次通过独立审查前，不开展新的真机 CAD 实验**（闸门已强制生效）。

---

# 第四轮整改（第三轮审查之后）

第三轮审查的判定：**安全审查仍为 FAIL；停止称"全绿"**——只有**编译**与**受限自检**是绿的。
已逐条核实并整改。**P1 仍为 INCOMPLETE，G01/G02 仍未通过。**

## 1. 现场入口已冻结（无可绕过开关）

| 处置 | 说明 |
|---|---|
| 移除 `CBRIDGE_ACK_UNREVIEWED_LIVE=1` | 该开关让自动化绕过**未解决的安全阻塞**，正是闸门要防的失效模式。**已删除**；解除只能通过**代码变更 + 审查** |
| 扩大闸门覆盖 | 原先只拦 COM 探针；现 **`dap-attach-*.py` 也被拦**。共 9 个现场脚本全部默认拒绝 |
| 证据 | `tooling/safety-gate-refusal.txt`（真实报错文本，含 4 项未完成事项） |
| 静态检查 | `selftest.sh` 校验「每个使用 COM 的现场脚本都被闸门拦住」且「闸门默认拒绝」 |

**未再执行任何真机 CAD 实验。**

## 2. `safe_process.py` 离线完成

| 审查指出的缺陷 | 核实 | 处置 |
|---|---|---|
| `terminate_owned()` 吞掉 `handle.wait()` 失败后仍删记录并返回成功 | ✅ 属实 | `wait()` 失败即**返回失败**并**保留**归属信息（不报告未经验证的成功） |
| `launch_and_record()` 未在读取元数据前拒绝**已退出**的句柄 | ✅ 属实 | 句柄 `poll()` 已退出即**拒绝记录**（该 PID 可能已被复用） |
| 进程枚举只在「非零退出**且**无输出」时失败 | ✅ 属实 | 改为**任何非零退出**或**输出不可解析**都失败；**部分结果不得当作真相** |
| 自检大多直接调 `_register()`，未走真实 launch 路径 | ✅ 属实 | 新增 **launch → register → audit → terminate** 端到端注入测试，以及 wait 失败、已退出句柄、exe 不匹配、枚举失败等用例 |
| token 迁移后遗留 `if owned is None`、未定义 `proc`、未校验 `owned_process(...)` | ✅ 属实（**真实 NameError**） | 已修复；并引入 **pyflakes** 静态检查，另发现并修复 `pid` / `frame_id` 未定义、`as_int` 先用后定义 |

自检由 28 项增至 **40 项，全部通过**。

## 3. repl 探针：结构性重构（不再依赖"教会 linter 接受"）

| 审查要求 | 处置 |
|---|---|
| 等**完整结果**而非 nonce 前缀 | 新增 `wait_for_envelope([...])`，要求 nonce + status + 几何 + 插件标记**全部**出现 |
| 失效事件检查需**有界**且包含意外 `stopped`/错误/过早 `CBDBG_DONE` | 新增 `invalidating_events(client, start, end)`，按 **recv_index 窗口**判定；涵盖 `continued`/`terminated`/`exited`/意外 `stopped`/`error`/过早 `CBDBG_DONE` |
| 必需检查可被提前 return 绕过 | **重构为 `run_session()` + 仅清理的 `finally` + 一次性 `finalize()`**；判定只在 `finalize()` 产生 |
| 从 `finally` 中**移除** `SystemExit`（而非让 linter 接受） | 已移除；`finally` **只做清理**，异常正常传播 |
| 保留不可变接收消息 + 本地到达序号与时间戳 | 新增 `SessionJournal`（**append-only**，**不从事件日志中删除证据**）；`DapClient` 增加本地 `recv_index` |
| `expected_circle_handle` 未填充 | 新增**独立通道**读取：用内置 Lisp 表达式经 `hover` 读取同一实体 handle，与插件读取的 handle **精确比对**；取不到独立值即**判失败**（不再退化为「存在即通过」） |
| 需显式要求**成功获取**锁与事务 | 新增 `lock_ms`/`tx_ms` 存在性检查；`disposed` 改为**如实反映每次释放结果**（`tr_disposed`/`lock_disposed`） |

离线假件测试：**32 项全通过**（`tooling/repl-probe-offline-tests.txt`），并已纳入 `selftest.sh`。

## 4. 插件执行上下文（第 4 项未完成）

- 新增 `ExecutionContextBaseline`（`[LispFunction("CBBASELINE")]`）：在**空闲命令行**记录原生/托管线程 id 作为基线。
- `LiveReadLispFunction` 现在**在访问任何文档/数据库之前**校验线程身份；**无基线或不匹配即拒绝**
  （`status=refused_context_unverified`）。先前仅在返回后报告，**不足以**阻止其本要防范的访问。
- 探针已改为**先记录基线**再触发夹具。

**仍未完成**：对照 Autodesk **受支持文档**核实该回调上下文的保证；补**空闲正对照**与**经插件**的前/后置观测。

## 5. 文档与历史证据的分离

| 处置 | 说明 |
|---|---|
| 撤回"未造成损害" | `SAFETY-INCIDENT-process-cleanup.md` 现记 **严重度：高**，并明确 **历史影响未知、无法事后确证**；"未发现"**已撤回**（"未发现"≠"未发生"）；**不声称**具体次数 |
| `safe_process.py` 引言 | 删除"从未观察到有害"的表述，改为「**是否关闭过用户会话未知且无法重建**；命令**按构造即不安全**，与结果无关」 |
| 区分历史证据与后改的源码 | 明确记录：**受保护基线测试与仪表化回调尚未运行**；**不得**用新代码声称旧结果覆盖了新逻辑 |
| 不再新增乐观总结 | 本文件记录**缺陷与撤回**，不记录"已完成" |

## 6. 仍然阻塞（下次审查的前置条件）

1. COM 探针改**有界工作进程**，并**强制** `HWND→自有 PID` 校验后才访问图面。
2. 插件回调上下文对照 Autodesk **受支持文档**核实；补空闲正对照。
3. 与项目所有者对齐**环境独占性**与既有环境阻塞。
4. 向用户**披露**不当清理与未知影响，并请求确认**独占、授权**的测试环境。

## 7. 当前真实状态

- **绿**：编译（两 Shell 0 错误、无 Autodesk 程序集泄漏）、离线自检（40 + 32 + 24 项）。
- **未通过**：安全审查；**所有真机入口已被闸门冻结**。
- **T01-3 / T01-5 = NOT_RUN**；**G01/G02 = NOT_RUN**。
- 清单校验通过、文件存在、检查计数**均不构成安全批准或 Gate 放行**。
