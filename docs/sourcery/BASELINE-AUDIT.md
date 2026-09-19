# Sourcery 基线审计报告

**审计分支**：`audit/sourcery-baseline`
**审计前基线**：`main` @ `592c6ff`，tag `baseline/pre-sourcery-audit`
**Sourcery 接入状态**：✅ GitHub App 已安装（仅授权本仓库）；⛔ 网页 Review Settings 与
Security Scan 尚未启用（需项目所有者填写，见 `docs/sourcery/DASHBOARD-SETTINGS.md`）
**Sourcery PR Review**：✅ 已在 PR #1 上运行，**中文**输出，提出 2 条行内评论，两条均**属实**并已整改
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
| Sourcery GitHub App 接入 | ✅ **已完成**（仅授权 `moshouhot/CadBridge`） |
| Sourcery PR Review（中文） | ✅ **已完成**：PR #1，2 条行内评论，均已整改 |
| Sourcery re-review（整改后） | ⏳ 见 §3.5 |
| Security Scan | ⛔ 未启用（需在仪表盘开启；Open Source 计划为受限预览） |
| 网页 Review Settings / Rules R1–R10 | ⛔ 未填写（需人工粘贴，文本已备好） |
| 固定流程 `branch → PR → Sourcery → 修复 → re-review → merge` | ✅ 已建立并在 PR #1 上实际跑通 |

**因此当前整体状态是：基线整改已完成并通过 Sourcery Review；网页侧配置（Review Rules、
Security Scan）仍待人工填写，故“完全接入”尚未达成。**

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

## 4. Sourcery Review 发现的问题（均属实，已整改）
PR #1 上 Sourcery 以**中文**给出了 Reviewer's Guide、摘要和 **2 条行内评论**。两条都指向我
新增的 `tools/check-live-gates.py`，**两条都属实**，我先复现再修复。

### S1（安全）— 闸门检查只比对方法名，未解析调用者

**Sourcery 指出**：AST 检查把任何最终方法名为 `require_safety_review_passed` 的调用都当作
安全闸门，不关心接收者是什么。

**复现**：在 `dap-probe.py` 里加一个无关对象，其同名方法什么都不做，并把真实调用改成
`helper.require_safety_review_passed(...)` —— 旧检查器输出 **PASS**（漏洞确认）。

**整改**：检查器现在**解析导入绑定** —— 只有当接收者是 `import safe_process` 绑定的名字
（或函数本身是从 `safe_process` 直接导入）时才计数。同名方法不再算数。

**验证**：上述攻击现在被检出（exit 1）；已固化为变异测试。

### S2（影响面）— 入口点靠硬编码文件名列表，新 harness 不会被检查

**Sourcery 指出**：检查器只遍历写死的文件名，新增一个不同文件名的实时入口点不会被访问，
“每个入口点都被闸门保护”的不变量会**静默失效**。

**复现**：新建 `tools/dap-live-newprobe.py`，直接 `subprocess.Popen([...acad.exe])` 且无闸门
—— 旧检查器输出 **PASS**（漏洞确认）。

**整改**：入口点改为**按行为发现**（AST：调用 `launch_and_record`/`launch_job_and_record`/
`DapClient`，或用 CAD 可执行文件作参数 spawn 子进程；shell：赋值 CAD 可执行路径或以其为首词
执行）。硬编码清单退化为**记录已审阅例外**（`NON_LIVE_ALLOWLIST` / `LIVE_EXCEPTIONS` /
`GATE_MECHANISM_FILES` / `RETIRED_HARNESSES`），而不再是检查范围的定义。清单与检测结果
**矛盾时直接失败**（例如把实际会启动 CAD 的文件列为 non-live 会报错），清单条目指向不存在的
文件也会报错。

**验证**：当前发现 **14 个**实时入口点并逐个校验；上述攻击被检出；已固化为变异测试。

> **注**：两条整改都同步加进了 `selftest.sh` 的变异测试集，因此这个缺陷类**不能**再悄悄回归。

---

### D5（中）— 本机账号名在公开仓库里泄漏了三次（已改为强制校验）

**这是本次工作自身造成的缺陷，如实记录。** 本机账号/机器名在公开仓库里泄漏了三次：

1. 4 个 P0/P1 证据文件（首次发布前修复）；
2. **修复第 1 次泄漏时发现的**：P1 的两个 `.raw` 证据文件（UTF-16）各一处，且它们是
   **manifest 引用**的证据；
3. 在修复第 1 次泄漏过程中新生成的 `docs/evidence/P2/.../build-modern.txt`，MSB3277 警告
   带出了 24 处机器路径。

**根因**：证据是真实命令在真实主机上跑出来的，机器路径天然携带账号名。因此“每次事后手工清理”
不是修复。

**整改**：
- 新增 `tools/redact-evidence.py`：脱敏并写出 `<file>.redaction.txt` 旁注（原始 SHA-256、
  存储后 SHA-256、命中次数、替换范围），使改动**可审计**；标识符经参数/环境变量传入，
  **刻意不写进代码**（该文件公开，写死等于重新发布要删的字符串）；`--check` 只报告不写入。
- `tools/selftest.sh` 新增**不变式**：任何被跟踪文件出现该标识符即失败。**未设置环境变量时显示
  `SKIP` 而非 `PASS`**，避免“没配置”被误认为“已验证干净”。
- 受影响的 manifest 已更新哈希并在 `redactions` 块逐条记录原始/存储哈希。

**负向测试（已录证）**：把标识符注入一个被跟踪文件 → 守卫失败、套件 `25 passed, 1 failed`
退出码 1；恢复后回到 `26 passed, 0 failed`。

---

## 5. 记录为"不修改"的发现（含理由）

| 发现 | 判断 | 理由 |
|---|---|---|
| `tools/*.py` 中 33 处 `except Exception` | **不修改** | 逐处检查后，绝大多数是"诊断探针不得因日志/清理失败而改变命令结果"，且都把失败原因写回返回值或 stderr（例如 `safe_process.terminate_owned` 在 `wait()` 失败时明确返回 False 并保留所有权，而不是报成功）。仅"批量替换为更窄异常"会降低健壮性且无收益。**已新增 R5 规则**约束未来新增的静默吞噬。 |
| `run-accoreconsole-test.sh` 未调用 `require_safety_review_passed` | **不修改（但记录）** | 它启动的是 `accoreconsole.exe`（无 GUI、无用户文档/配置），不是用户正在使用的 `acad.exe`；其注释中"绝不触碰用户会话"的说法**仅对不传 `--dwg` 时成立**。因此：① 保留其可用性（P1 的证据正是靠它产出，且不启动真机 GUI 会话）；② 在报告中明确该限度，**不**把"headless 所以绝对安全"当作已证明的命题。 |
| `_ps()` 使用 60s 超时的 PowerShell 枚举 | **不修改** | 超时返回 rc=1，调用方按 `EnumerationError` 处理并 **fail-closed**（拒绝终止），行为正确。 |
| `dap-session.py` 中 `time.sleep(1.5)` 等固定等待 | **不修改** | 属于探针的启发式等待，非产品代码；改为事件驱动需要真实的 DAP 行为数据，而真机运行当前被安全闸阻塞。记录为待 P2 处理。 |
| 文档/证据中的本机绝对路径（`D:\CAD APPLOAD\...` 等） | **不修改** | 附加正确性依赖这些路径，且它们不是凭据。已在 `PUBLICATION.md` §4 明确不声称"不含本机信息"。 |

---

## 6. 测试与验证证据

| 项目 | 命令 | 结果 |
|---|---|---|
| 工具自检（含 **6** 个变异测试 + 隐私守卫） | `bash tools/selftest.sh` | **26 passed, 0 failed**，exit 0 |
| 半径策略回归测试 | `bash tests/run-radius-policy-tests.sh` | **14 passed, 0 failed**，exit 0 |
| 变异：删 `dap-probe` 闸门 | `check-live-gates.py` 对损坏副本 | **检出**（exit 1） |
| 变异：闸门改注释 | 同上 | **检出**（exit 1） |
| 变异：退休 harness 去掉 `--gate` | 同上 | **检出**（exit 1） |
| 变异：同名方法冒充闸门（S1） | 同上 | **检出**（exit 1） |
| 变异：新增未设闸门的 harness（S2） | 同上 | **检出**（exit 1） |
| 变异：`Cancel` 改回创建 | 半径测试对损坏源码 | **检出**（2 项失败，exit 1） |
| 负向测试：标识符重新出现 | `redact-evidence.py --check` + 套件 | **检出**（套件 25 passed/1 failed，exit 1） |
| Legacy 插件构建 | `dotnet build ...Legacy.csproj -c Release` | 0 警告 0 错误 |
| Modern 插件构建 | `dotnet build ...Modern.csproj -c Release` | 0 错误（3 个已记录的 MSB3277 警告） |
| 包内无 Autodesk DLL | `find src/*/bin -name "ac*.dll"` | 无（符合 `Private=false` 约束） |
| 静态检查 | `python -m pyflakes tools/*.py` | 干净 |
| 凭据 / 标识符扫描 | 对全部已提交 blob | 无命中 |
| 证据哈希 | blob 级 SHA-256 对照 manifest | **161/161** 一致（2 个有意排除） |

---

## 7. 剩余风险与未完成项

1. **网页侧配置未完成**：Review Rules（R1–R10）、Review profile（基线期 Verbose）、
   Review language（中文）、Security Scan 开关均需人工在 Sourcery 仪表盘填写。文本已备在
   `docs/sourcery/DASHBOARD-SETTINGS.md`。
2. **Security Scan 未运行**：它只扫默认分支，且 Open Source 计划为受限预览（最多 3 仓库、
   每周两次、仪表盘最多 10 条）。**可见发现数受计划限制**必须如实记录，不得把"只看到 10 条"
   当作"只有 10 个问题"。
3. **本地 Sourcery CLI 不等于 IDE/App Review**：`sourcery-cli` 是 **Python 专用**重构工具，
   且对未登录场景要求 token。本仓库主体是 C#，CLI **无法**替代 App Review。
4. **真机行为未验证**：本次**未**运行任何 CAD。D4 的修复只在纯逻辑层面验证。
5. **`run-accoreconsole-test.sh` 的限度已记录但未消除**：它接受 `--dwg`，"headless 所以安全"
   不是已证明的命题。它被列为**有理由的例外**而非"已通过闸门"。
6. **P1 阶段本身仍为 INCOMPLETE**（G01/G02 NOT_RUN），与本次审计无关但状态未变。

---

## 8. 固定流程（已建立并在 PR #1 上跑通）

```
feature branch → commit → PR → Sourcery Review → 修复
              → Sourcery re-review → merge main
```

- 每次 push 自动触发 re-review，**自动上限 5 次**，超过后状态检查显示 `Skipped`；
  需要完整重跑时评论 `@sourcery-ai review`（会重置计数器）。
- 状态检查名为 **`Sourcery review`**。它**不会阻塞合并**，所以"检查是绿的"**不等于**
  "评论已处理完" —— 合并前必须逐条核对行内评论是否已解决或已说明不修改理由。
- 本项目约定：**不接受** Sourcery 自动 approve 代替人工确认。

---

## 9. 需要项目所有者操作的一步

GitHub App 已安装。剩下的是网页配置：

1. <https://app.sourcery.ai/dashboard/review-settings> → Review profile 选 **Verbose**、
   语言选 **中文**、在 **Review rules** 标签页粘贴 `DASHBOARD-SETTINGS.md` §4 的 R1–R10。
2. <https://app.sourcery.ai/dashboard/security/repositories> → 为 `CadBridge` 启用
   **Scanning enabled**（Open Source 计划无按需触发按钮，为每周两次）。
3. 配置完成后回填 `DASHBOARD-SETTINGS.md` §7 的「配置回执」表。
