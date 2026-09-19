# Sourcery 基线审计报告

**审计分支**：`audit/sourcery-baseline`
**审计前基线**：`main` @ `592c6ff`，tag `baseline/pre-sourcery-audit`
**Sourcery 接入状态**：**未完成** —— GitHub App 尚未安装（需项目所有者在网页操作）
**真机状态**：本次工作**未启动**任何 AutoCAD / CoreConsole 会话

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 可回退基线（commit + tag + 仓库外备份） | ✅ 已建立并验证 |
| 现有核心代码基线审计（有证据） | ✅ 已完成一轮 |
| 发现并整改的真实缺陷 | ✅ 4 个（含 2 个高严重度） |
| 明确记录为"不修改"的发现 | ✅ 见 §4 |
| 项目测试 + 针对性回归测试 | ✅ 通过（23 + 14 项，含变异测试） |
| Sourcery GitHub App 接入 | ⛔ **未完成**（待人工授权） |
| 基线整改的 Sourcery PR Review | ⛔ **未完成**（依赖上一项） |
| 固定流程 `branch → PR → Sourcery → 修复 → re-review → merge` | ⚠️ 文档与规则已就绪，待 App 接入后生效 |

**因此当前整体状态是：未完成（blocked on Sourcery authorization）。**

---

## 2. 审计范围与方法

按风险优先级审计了以下区域（依据实际代码识别，不是通用清单）：

| 区域 | 文件 | 方法 |
|---|---|---|
| 进程管理 / 所有权 | `tools/safe_process.py` (1119 行) | 逐段阅读 + 离线自检 + 变异测试 |
| 有界子进程 / 超时 | `tools/bounded_worker.py` | 阅读 + 自检 |
| DAP 分帧 / 超时 / 重同步 | `tools/dap-probe.py`、`dap-session.py` | 阅读帧解析与错误路径 |
| 证据持久化 | `tools/make-manifest.py` | 阅读 + **故障注入测试** |
| 测试本身的可信度 | `tools/selftest.sh` | **变异测试**（故意破坏被测保护） |
| 插件边界条件 / 取消语义 | `src/Plugin.Shared/DiagnosticsCommands.cs` | 阅读 + 纯逻辑回归测试 |
| 并发 / 重入 / 线程身份 | `src/Plugin.Shared/LiveReadLispFunction.cs`、`ExecutionContextBaseline.cs` | 阅读 |
| 运行时自报（防止常量冒充测量值） | `src/Plugin.Shared/RuntimeInfo.cs` | 阅读 |
| 静态检查 | `tools/*.py` | pyflakes + `compileall` |
| 凭据泄漏 | 全部 208 个已提交 blob | 二进制安全模式扫描 |

**未审计**（本项目尚无对应实现，不是遗漏）：认证/授权/Token/API Key/Session/CSRF/网络 API/
数据库 —— 这些属于 PRD 中 P2+ 的 Host、Registry、Pipe 认证范围，当前代码库尚未实现。

---

## 3. 发现并整改的真实缺陷

### D1（高）— 自检套件把"安全闸已被移除"报告为 PASS

**证据**：在 `tools/dap-probe.py` 中删除 `sp.require_safety_review_passed(...)` 调用后，
`tools/selftest.sh` 仍然输出 `PASS every live CAD harness is gated on the safety review` 和
`self-tests: 18 passed, 0 failed`，退出码 0。

**根因**（两个独立缺陷叠加）：
1. 该断言写在**生成的假 `accoreconsole.exe`** 里，而假文件用 `<<'EOF'`（引号）写入，
   `$TOOLS` 不会被展开；且 `selftest.sh` 从未 `export TOOLS`。子进程实际 grep 的是
   `/dap-probe.py`（不存在）。更糟的是失败分支在**子 shell** 里自增 `GATE_VIOLATIONS`，
   父进程永远看不到。
2. 断言用 `grep -q 'require_safety_review_passed'` —— **注释也能满足它**。

**影响**：这是"安全关键断言无法失败"，直接导致实时 CAD 安全闸的失效不会被发现。属于本项目
最忌讳的"看起来有保护，实际没有"。

**整改**：
- 新增 `tools/check-live-gates.py`：用 **AST** 要求真实调用（注释/字符串不满足），
  校验 `dap-probe.py` 的闸门是**条件式**（仅在 live intent 时拦截），校验退休的 shell harness
  走 `safe_process.py --gate`，并用 AST 检查**真实读取**环境变量（避免把说明文字误判为绕过）。
- `tools/selftest.sh` 中把断言移到**父进程**，并新增 **3 个变异测试**：分别删除
  `dap-probe.py` 闸门、把 `t01-5-definitive.py` 闸门改成注释、移除退休 harness 的闸门 ——
  三者都必须被检出，否则套件失败。
- 删除重复的测试项（原第 137/138 行同名重复）。

**验证**：变异测试 3/3 检出；真实树 PASS；套件 23 passed / 0 failed。

### D2（高）— 证据哈希在全新 clone 上无法验证（`core.autocrlf` 字节改写）

**证据**：`docs/evidence/P0/.../raw/os.json` 工作区 204 字节 / 7 个 CR，提交后的 blob 为
197 字节 / 0 个 CR。全仓库 **145 个 artifact 中 56 个**哈希不匹配。

**根因**：仓库创建于 `core.autocrlf=true`，Git 存储 blob 时把 CRLF 改写为 LF，于是
manifest 里记录的 SHA-256 与 clone 得到的字节不一致。README/文档声称的"可用哈希验证证据"
因此**不成立**。

**整改**：新增 `.gitattributes`，对字节即证据的路径关闭 EOL 转换
（`docs/evidence/**`、`*.jsonl`、`*.scr` 为 `-text`；`*.sh` 强制 LF；`*.ps1/*.cmd` 强制 CRLF；
`*.dll/*.dwg` 为 binary），并执行 `git add --renormalize .` 恢复原始字节。

**验证**：`HEAD` 的 145 个证据 artifact 哈希全部与 manifest 一致（仅 2 个有意排除的文件例外，
见 `docs/evidence/PUBLICATION.md` §2.4）。

### D3（中）— 重新生成 manifest 会静默抹掉非派生内容

**证据**：对 `docs/evidence/P0/.../` 直接运行 `make-manifest.py`（不带 `--status-file`），
manifest 从 13 个测试结论变成 0 个，`redactions` provenance 块被删除，而工具仍然打印
`validation OK`。

**根因**：`tests` 只在传入 `--status-file` 时赋值，否则硬编码为空列表；`redactions` 完全不
参与重建。二者都不是能从目录派生的内容。

**整改**：
- 未传 `--status-file` 时，**继承**已有 manifest 的 `tests`；`redactions` 始终保留并补一条说明。
- 写入改为**原子**（`tempfile.mkstemp` + `fsync` + `os.replace`），失败时删除临时文件、
  不动上一份 manifest。
- 新增 2 个回归测试：① 重新生成必须保留 `tests` 与 `redactions`；② 注入序列化失败后，
  上一份 manifest 必须**逐字节不变**且不留临时文件。

### D4（中）— 取消交互式输入仍会创建图元

**证据**：`DiagnosticsCommands.CircleWithArgs()` 原逻辑为

```csharp
double radius = 10.0;
var res = ed.GetDouble(opts);
if (res.Status == PromptStatus.OK) { radius = res.Value; }
CreateCircle(100.0, 100.0, radius);   // 对所有状态都执行
```

**根因**：未区分"用户接受默认值"（`PromptStatus.None`）与"用户按 Esc 取消"（`Cancel`）或
"提示失败"（`Error`）。取消命令仍然画出半径 10 的圆，违反"被拒绝的命令不得留下副作用"。

**整改**：抽出纯函数 `src/Plugin.Shared/RadiusPromptPolicy.cs`，使用**真实的**
`Autodesk.AutoCAD.EditorInput.PromptStatus` 枚举（未使用手抄的数值常量）：
只有 `OK` 与 `None` 允许继续；`Cancel`/`Error`/`Keyword`/`Modeless` 及**任何未枚举状态**
一律 fail-closed 拒绝并返回，不创建任何图元。

**验证**：新增 `tests/RadiusPromptPolicyTests`（14 项，全部通过），并在 csproj 中直接
编译生产源码（不复制、不重写）。**变异测试**：把 `Cancel` 改回"用默认值继续"后，测试
2 项失败、退出码 1 —— 证明测试确实能捕获该缺陷。

> **限度声明**：这些测试是**纯逻辑**覆盖，使用真实枚举值，但**不启动 AutoCAD**，
> 因此**不证明**真机交互取消行为。真机验证仍在 P1/P2 计划内，未被本测试替代。

---

## 4. 记录为"不修改"的发现（含理由）

| 发现 | 判断 | 理由 |
|---|---|---|
| `tools/*.py` 中 33 处 `except Exception` | **不修改** | 逐处检查后，绝大多数是"诊断探针不得因日志/清理失败而改变命令结果"，且都把失败原因写回返回值或 stderr（例如 `safe_process.terminate_owned` 在 `wait()` 失败时明确返回 False 并保留所有权，而不是报成功）。仅"批量替换为更窄异常"会降低健壮性且无收益。**已新增 R5 规则**约束未来新增的静默吞噬。 |
| `run-accoreconsole-test.sh` 未调用 `require_safety_review_passed` | **不修改（但记录）** | 它启动的是 `accoreconsole.exe`（无 GUI、无用户文档/配置），不是用户正在使用的 `acad.exe`；其注释中"绝不触碰用户会话"的说法**仅对不传 `--dwg` 时成立**。因此：① 保留其可用性（P1 的证据正是靠它产出，且不启动真机 GUI 会话）；② 在报告中明确该限度，**不**把"headless 所以绝对安全"当作已证明的命题。 |
| `_ps()` 使用 60s 超时的 PowerShell 枚举 | **不修改** | 超时返回 rc=1，调用方按 `EnumerationError` 处理并 **fail-closed**（拒绝终止），行为正确。 |
| `dap-session.py` 中 `time.sleep(1.5)` 等固定等待 | **不修改** | 属于探针的启发式等待，非产品代码；改为事件驱动需要真实的 DAP 行为数据，而真机运行当前被安全闸阻塞。记录为待 P2 处理。 |
| 文档/证据中的本机绝对路径（`D:\CAD APPLOAD\...` 等） | **不修改** | 附加正确性依赖这些路径，且它们不是凭据。已在 `PUBLICATION.md` §4 明确不声称"不含本机信息"。 |

---

## 5. 测试与验证证据

| 项目 | 命令 | 结果 |
|---|---|---|
| 工具自检（含变异测试） | `bash tools/selftest.sh` | **23 passed, 0 failed**，exit 0 |
| 半径策略回归测试 | `bash tests/run-radius-policy-tests.sh` | **14 passed, 0 failed**，exit 0 |
| 变异：删 `dap-probe` 闸门 | `check-live-gates.py` 对损坏副本 | **检出**（exit 1） |
| 变异：闸门改注释 | 同上 | **检出**（exit 1） |
| 变异：`Cancel` 改回创建 | 半径测试对损坏源码 | **检出**（2 项失败，exit 1） |
| Legacy 插件构建 | `dotnet build ...Legacy.csproj -c Release` | 0 警告 0 错误 |
| Modern 插件构建 | `dotnet build ...Modern.csproj -c Release` | 0 错误（3 个已记录的 MSB3277 警告） |
| 包内无 Autodesk DLL | `find src/*/bin -name "ac*.dll"` | 无（符合 `Private=false` 约束） |
| 静态检查 | `python -m pyflakes tools/*.py` | 干净 |
| 凭据扫描 | 对 `HEAD` 的 208 个 blob | 无命中 |
| 证据哈希 | blob 级 SHA-256 对照 manifest | 145/145 一致（2 个有意排除） |

---

## 6. 剩余风险与未完成项

1. **Sourcery 未接入**：GitHub App 需项目所有者在网页安装（见 §7）。在此之前，所有
   "Sourcery 审计"结论都**不存在** —— 本次基线审计是**人工 + 工具**完成的，不是 Sourcery 做的。
2. **本地 Sourcery CLI 不等于 IDE/App Review**：`sourcery-cli` 是 **Python 专用**重构工具，
   且对非公开/未登录场景要求 token。本仓库主体是 C#，CLI **无法**替代 Sourcery App 的
   AI Review。不得把 CLI 结果当作 App Review 证据。
3. **Security Scan 未运行**：它只扫默认分支，且 Open Source 计划为受限预览（最多 3 仓库、
   每周两次、仪表盘最多 10 条）。需接入后启用；**可见发现数受计划限制**必须如实记录。
4. **真机行为未验证**：本次**未**运行任何 CAD。D4 的修复只在纯逻辑层面验证。
5. **P1 阶段本身仍为 INCOMPLETE**（G01/G02 NOT_RUN），与本次审计无关但状态未变。

---

## 7. 需要项目所有者操作的一步

安装 Sourcery GitHub App（仅授权本仓库）：

1. 打开 <https://github.com/apps/sourcery-ai/installations/new>
2. 用 **moshouhot** 登录
3. **Repository access** → **Only select repositories** → 只勾选 **`moshouhot/CadBridge`**
4. 确认权限 → **Install**

随后按 `docs/sourcery/DASHBOARD-SETTINGS.md` 填写 Review Settings（基线期用 **Verbose**、
语言选**中文**、粘贴 R1–R10 规则）并启用 Security Scan。
