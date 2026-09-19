# 第三方审计：Live Safety Gate Closeout

日期：2026-09-18  
审计角色：第三审计员  
审计范围：P1 live harness 安全闭环；**本轮未启动、附加、关闭或终止任何真实 AutoCAD**。

## 1. 最终判定

**Live Safety Gate = BLOCKED**

不是因为离线整改失败，而是因为“允许恢复真机测试”的最后证据仍必须来自受控、独占、授权的真实环境。本轮完成了代码审查、静态检查、离线故障注入、两 Shell 编译和 Autodesk 官方文档核对；没有用这些离线证据冒充 T01-3/T01-5 真机证据。

保持项目状态：

- `P1 = INCOMPLETE`
- `T01-3 = NOT_RUN`
- `T01-5 = NOT_RUN`
- `G01 = NOT_RUN`
- `G02 = NOT_RUN`

## 2. 本轮独立发现

### F-01 HIGH：live 入口冻结声明不完整

`tools/run-acad-gui-test.sh` 可直接启动 `acad.exe`，此前不经过硬安全闸门。更严重的是，该脚本启动后会按安装目录重新扫描进程并取第一条匹配 PID，超时后再 `taskkill /PID`。如果用户在预检查之后启动同版本 AutoCAD，脚本存在把第三方/用户实例误当作自己的竞态。

处置：**永久退休该 legacy shell harness**。保留同名文件只用于明确拒绝旧命令；未来 GUI 真机测试必须重新建立 ownership-preserving Python harness，不能恢复旧 PID 扫描/终止逻辑。

因此旧证据中的 `T01-2-partial PASS` 只能视为“当时观察到两个测试会话能启动并退出”，**不能再作为安全归属/只终止自己进程的 PASS 证据**。

同时发现通用 `tools/dap-probe.py` 在 `--attach-pid`、`--program` 或传入 `acad.exe` adapter 参数时也可进入 live 模式，但未纳入闸门。现已按 live intent 加硬闸门；纯 adapter 离线协议探测仍可使用。

### F-02 HIGH：进程枚举存在 fail-open 解析

旧 `ProcessProvider` 遇到以 `{` 开头但无法 `json.loads` 的输出时会静默跳过；`by_pid` 最终可能将“枚举输出损坏”误判为“PID 不存在”。这违反 ownership/preflight 的 fail-closed 原则。

处置：新增严格 `_parse_process_rows()`；任何非空行都必须是 JSON object，Malformed / partial / non-object / invalid PID 一律 `EnumerationError`。多个旧 live probe 自己实现的 PowerShell `acad_pids()` 也改为统一调用 `safe_process.install_pids()`，枚举失败不再返回空列表。

### F-03 HIGH：COM timeout 使用不可收回 daemon thread

旧暂停探针把 COM 调用放在 daemon thread。超时只停止等待，worker 仍可能继续访问 AutoCAD，无法证明测量窗口结束后没有额外副作用。

处置：新增：

- `tools/bounded_worker.py`
- `tools/com_read_worker.py`

父进程只管理**自己启动的 worker 子进程**。超时后 terminate/kill 的对象只能是 worker，自身绝不终止 AutoCAD。COM worker 在任何 `ActiveDocument/ModelSpace` 访问之前必须依次完成：

1. expected PID 存在；
2. creation time 精确匹配；
3. executable path 精确匹配；
4. `GetActiveObject` 后 `HWND -> PID` 精确等于 expected PID。

任一失败均拒绝图面访问。

已迁移涉及 COM timeout 的主要 T01-5 probe；当前搜索到的 `threading.Thread` 仅剩 DAP client 自己的 stdout/stderr reader，不直接进行 COM/CAD DB 操作。

### F-04 HIGH：执行上下文不能只用“线程相同”证明

原 `ExecutionContextBaseline` 只记录 native/managed thread id。线程相同不等于 AutoCAD execution context 合法。

Autodesk 官方文档明确区分 application context 与 document context，并提供 `isApplicationContext()` 查询；.NET 暴露对应 `Application.DocumentManager.IsApplicationContext`。文档同时说明：查询/只读操作不要求显式锁，AutoCAD commands / ObjectARX commands / AutoLISP functions 的基本文档锁由 AutoCAD 自动处理。

处置：

- baseline 现在必须成功读取 `IsApplicationContext`；
- application context 拒绝成为 idle baseline；
- live callback 在**任何 document/database API 前**再次查询 context；
- 只有 document context + baseline thread identity 同时匹配才继续；
- `LiveReadLispFunction` 是纯读 probe，移除显式 `LockDocument()`，只开只读 Transaction，输出 `lock_mode=not_requested_read_only`。

官方参考：

- https://help.autodesk.com/cloudhelp/2022/ENU/OARX-DevGuide/files/GUID-4558026D-4858-45C8-BC0F-6C323577BD45.htm
- https://help.autodesk.com/cloudhelp/2022/ENU/OARX-DevGuide/files/GUID-57178B24-8CD5-4BBD-85A6-7F54BB07112F.htm
- https://help.autodesk.com/cloudhelp/2026/CSY/OARX-DevGuide-Managed/files/GUID-A2CD7540-69C5-4085-BCE8-2A8ACE16BFDD.htm

**限制**：这些官方资料支持一般 execution-context / locking 规则，但没有明确声明“DAP breakpoint 停止时，通过 `evaluate/repl` 重入一个 managed `[LispFunction]` 并读取数据库”这一具体组合被官方保证安全。因此这条只能到“设计与静态实现对齐”，不能替代真机 PoC。

### F-05 MEDIUM：全局解闸设计风险

如果安全闸门未来用单个 global bool 或环境变量解开，一次批准会同时开放所有历史 probe，风险过大。

处置：`safe_process.py` 改为**逐 harness 代码 allowlist**；当前 allowlist 为空。没有环境变量 bypass。后续只能在具体脚本完成代码审查和受控真机预演后，单独加入 allowlist。

### F-06 REMAINING：launcher-stub / child handoff ownership 尚未闭环

`launch_and_record()` 只认 `Popen` 返回 PID，不会猜测/收养同安装目录的另一个进程，这是正确的 fail-closed 方向。但 AutoCAD 可能存在 launcher stub / handoff：如果原 PID 很快退出并产生真正宿主子进程，函数会返回 `None`，而新宿主进程可能仍存活但不再有 ownership token。

当前实现不会因此误杀别人的 CAD，但可能产生**无法安全清理的测试孤儿进程**。在新的 GUI harness 上线前，需要用 Windows Job Object、可证明的 parent/child lineage，或其他受支持的 ownership transfer 机制解决；不能退回“扫描安装目录后认领第一个 acad.exe”。

## 3. 离线复验结果

| 检查 | 结果 | 说明 |
|---|---|---|
| `safe_process.py --self-test` | **PASS 43/43** | fabricated process tables；不启动真实进程 |
| `bounded_worker.py --self-test` | **PASS 6/6** | 包含 hard timeout + worker exit confirmed |
| `test-repl-probe-offline.py` | **PASS 32/32** | Fake DAP/journal/mandatory checks |
| targeted `pyflakes` | **PASS / 0 warning** | 安全/live probe 目标文件 |
| Legacy net48 build | **PASS** | 0 warnings / 0 errors |
| Modern net8 build | **PASS with warnings** | 0 errors；存在 Microsoft.VisualBasic/System.Drawing/WindowsBase 版本冲突 warning group，需后续单独处置 |
| COM worker 不存在 PID | **PASS (拒绝)** | `refused_identity`，未进入文档访问 |
| live safety gate negative check | **PASS (拒绝)** | 当前无任何 harness allowlisted |

以上均为离线/编译证据，不是 G01/G02 的真机 PASS。

## 4. Gate 分解

| 子项 | 当前状态 |
|---|---|
| 进程 ownership token / retained handle / PID reuse fail-closed | PASS（离线） |
| 进程枚举 malformed/partial output fail-closed | PASS（离线） |
| 已知 live 入口默认冻结 | PASS（静态）；legacy GUI shell 已退休 |
| 无环境变量 bypass | PASS |
| bounded worker 能在超时后确认 worker 退出 | PASS（离线） |
| COM 在文档访问前执行 PID/creation/exe/HWND->PID 校验 | PASS（代码/离线拒绝）；真实多实例 NOT_RUN |
| AutoCAD execution-context 官方规则纳入 guard | PASS（设计/编译）；DAP pause 实际语义 NOT_RUN |
| idle `CBBASELINE` 正对照 + paused callback 对照 | NOT_RUN |
| 新 ownership-preserving GUI harness | BLOCKED / 尚未实现 |
| 独占、授权测试环境 | BLOCKED / 项目所有者尚未确认 |
| T01-3 / T01-5 真机复验 | NOT_RUN |

所以总判定必须保持：**BLOCKED**。

## 5. 允许下一次 live run 之前的强制清单

只有全部满足才允许选中的**单个 harness**进入 allowlist：

1. 项目所有者明确确认当前窗口是独占、授权测试环境；
2. 没有第三方 Agent / 软件并发做 AutoCAD `hosttest-*` 或修改 CAD profile；
3. 明确测试 AutoCAD 完整版本/补丁/安装路径与许可证来源；
4. 仅使用 disposable DWG / fixture，不接触生产 DWG；
5. 新 GUI harness 能证明自己启动的真实宿主 ownership，解决 launcher handoff；
6. cleanup 只能通过 retained ownership，禁止按进程名终止、禁止目录扫描后认领；
7. COM worker 只能访问 HWND 所属 PID 与 owned PID 精确匹配的实例；
8. `CBBASELINE` 必须先在空闲 document context 实测成功；
9. paused `[LispFunction]` 必须在访问 DB 前报告 `IsApplicationContext == false` 且 thread baseline 匹配；
10. worker timeout 后必须确认 worker 已退出；无法确认记 `outcome_unknown` 并停止后续 live 动作；
11. 测试期间不得自动修改 `SECURELOAD` / `TRUSTEDPATHS` / `LISPSYS` /永久自动加载；
12. 首次 live run 只做只读/最小 fixture，成功后再逐步扩大；每轮保存 PID/start time/path/HWND、源码/hash、退出状态和清理证据。

## 6. 历史事故结论保持不变

早期 `Stop-Process -Force` 按进程名清理的设计**按构造即不安全**。历史上是否真的关闭过用户 CAD：**unknown / unconfirmable**。本轮整改不能反向证明历史无损，也不得把“未发现”改写为“未发生”。

## 7. 下一步

不是继续 P2/P3，也不是直接恢复全部 probe。下一步是：

1. 项目所有者确认独占、授权 test window；
2. 新建并审计 ownership-preserving GUI harness（重点解决 launcher/child handoff）；
3. 只 allowlist 该一个 harness；
4. 先做 idle baseline + read-only 正对照；
5. 再恢复 T01-3/T01-5 的最小真机复验。

在这些完成以前，`P1 INCOMPLETE / G01 NOT_RUN / G02 NOT_RUN` 是唯一可辩护状态。
