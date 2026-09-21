# Sourcery 接入设置（GitHub App）

本文件是**需要手工在 Sourcery 网页控制台填写**的设置的唯一权威副本，外加一套可直接粘贴的
Review Rules。仓库里的 `.sourcery.yaml` **不配置** GitHub App —— 它只配置本地 `sourcery`
CLI（Python 专用重构工具），两者互不影响。

> 状态：**部分完成**。GitHub App 已在本仓库产生 PR Review（PR #1，中文）；但 Review profile、
> Review language、Review rules R1–R10、Security Scan 开关、IDE 文件审查**均尚未完成**，
> 需项目所有者手工填写。本文件记录"应该怎么配"，不等于"已经配好"。
> 完成情况以 §7「配置回执」为准。

---

## 1. 安装 GitHub App（✅ 已完成，范围待人工确认）

**已完成**：App 已在本仓库运行（PR #1 上有 `sourcery-ai` 的 `Sourcery review` 检查与中文评论）。

**待人工确认**：安装范围是否为"仅本仓库"。GitHub API 不向普通用户 token 暴露该信息
（`GET /repos/{owner}/{repo}/installation` 需 App JWT），因此**本会话无法验证**。
请到 <https://github.com/settings/installations> → Sourcery → 确认
**Repository access = Only select repositories** 且仅勾选 `CadBridge`。

<details>
<summary>原始安装步骤（供重新安装或核对时参考）</summary>

1. 打开 <https://github.com/apps/sourcery-ai/installations/new>
2. 用 **moshouhot** 账号登录。
3. **Repository access** 选择 **Only select repositories** → 只勾选 **`moshouhot/CadBridge`**。
   不要选 All repositories：本项目是公开仓库，最小授权即可。
4. 检查权限列表，确认后点 **Install**。

</details>

安装后回到 <https://app.sourcery.ai/dashboard/repo-settings> 选中 `CadBridge`。

> 免费额度：CadBridge 是 public 仓库，可走 **Open Source（免费）** 计划，包含 PR Review、
> Reviewer's Guide、Review Rules、IDE Review。Security Scan 在该计划下是**受限预览**
> （最多 3 个仓库、每周两次、仪表盘最多显示 10 条发现）。**不要**为此开通付费计划或试用，
> 除非项目所有者明确要求。

---

## 2. Review Settings（首次基线审计：偏严格）

打开 <https://app.sourcery.ai/dashboard/review-settings>。

| 设置项 | 首次基线审计建议值 | 理由 |
|---|---|---|
| Enable Sourcery on pull requests | **On** | 必需 |
| Enable AI review comments | **On** | 行内评论是本项目的价值来源 |
| Enable pull request summary | **On** | 写入 PR 描述顶部 |
| Enable review guide | **On** | 逐文件说明改动与验证方式 |
| Enable sequence diagrams | **On** | 本项目有 DAP 时序、进程归属等控制流 |
| Enable tips and commands | **On** | 便于用 `@sourcery-ai review` 手动重跑 |
| **Review profile** | **Verbose** | 基线审计要"尽量多发现"；日常再降到 Balanced |
| Review language | **中文（Chinese）** | 见 §3 |
| Let Sourcery approve pull requests | **Off** | 本项目要求人工确认 Sourcery 评论已处理，不接受自动 approve |
| Review draft pull requests | Off（默认） | — |
| Path Filters（排除） | `docs/evidence/**`、`**/bin/**`、`**/obj/**` | 证据是按哈希校验的字节，不是重构对象 |

**日常使用**：基线审计合并后，把 Review profile 从 Verbose 调回 **Balanced**（默认值）。
Verbose 会包含 nitpick，长期使用噪声过大。

---

## 3. Review Language（中文）

在 Review Settings 的 **General** 标签页，**Select language** 下拉框选择中文。
语言设置作用于 summary、reviewer's guide 和行内评论。

> 说明：文档未列出完整语言清单（"Open the dropdown for the current list"）。**如果下拉框中
> 没有中文**，则保留 English 并在下方「配置回执」中如实记录为"中文不可用"，不要假装已配置。

---

## 4. Review Rules（可直接粘贴）

路径模式与规则正文分别填入 Review Settings 的 **Review rules** 标签页。每条规则：
**Path patterns** + **Rule**（≤3000 字符）。

这些规则针对本仓库真实存在的风险面（证据完整性、进程归属、实时 CAD 安全闸、异常吞噬、
边界条件、状态持久化、资源释放、并发、回归测试），不是泛泛而谈的通用条款。

### R1 — 证据完整性：不得静默改写已捕获的证据

- **Path patterns**：`docs/evidence/**`
- **Rule**：

> 本目录存放的是"已捕获证据"：`manifest.json` 用 SHA-256 记录每个 artifact 的字节。
> 因此，任何对已有 artifact 内容的修改都必须同时更新对应的 manifest 哈希，并且必须保留
> `redactions` 块说明"发布字节与原始捕获字节不同"。禁止在未更新哈希的情况下改写 artifact；
> 禁止删除 `redactions` 块。如果改动是脱敏（例如移除本机用户名/机器名），必须同时更新哈希
> 与 provenance，且不得在文件里重新写出被脱敏的字面标识符本身。

### R2 — 证据不得被"重新生成"覆盖为非派生内容

- **Path patterns**：`tools/make-manifest.py`
- **Rule**：

> manifest 里同时存在**可派生**内容（artifacts 哈希、validation）和**不可派生**内容
> （tests 测试结论、redactions provenance）。重新生成 manifest 时，不可派生内容必须被保留
> 或被显式替换，绝不允许因为"没传某个参数"而静默清空。任何写入 manifest 的操作必须是原子的
> （临时文件 + 原子替换），失败时不得留下截断的 manifest，也不得损坏上一份。

### R3 — 进程归属：终止进程必须可证明归属，禁止按名字杀

- **Path patterns**：`tools/**`
- **Rule**：

> 本项目绝不允许关闭用户正在使用的 AutoCAD。因此：禁止按镜像名终止进程（如
> `Stop-Process -Name acad`、`taskkill /IM`）；禁止通过扫描安装目录来"认领"进程
> （如取匹配列表的第 0 个）；终止必须走**本进程私有注册表**里的不透明 token，并在终止前
> 重新校验 PID + 创建时间 + 可执行文件路径。任何"归属无法证明"的情况都必须**拒绝终止**并
> 报告，而不是降级为按 PID 直接杀。COM 只允许 `GetActiveObject`，禁止 `Dispatch` 兜底
> （Dispatch 可能启动或选中非预期实例）。

### R4 — 实时 CAD 安全闸不得被绕过

- **Path patterns**：`tools/**`
- **Rule**：

> 任何可能产生真实 CAD 会话的入口（启动/附加 acad.exe、让 debug adapter 自行拉起宿主）
> 都必须调用 `safe_process.require_safety_review_passed(...)`。该调用必须是**真实调用**，
> 不能被注释、字符串或文档说明替代。禁止引入任何环境变量开关来绕过该闸门
> （例如 `CBRIDGE_ACK_*`）。`dap-probe.py` 的闸门必须是**条件式**的：仅在 live intent
> （attach/program/acad.exe）成立时才拦截，纯离线协议探测不拦截。

### R5 — 异常不得被静默吞噬

- **Path patterns**：`tools/**`
- **Rule**：

> 捕获异常后不得只 `pass` 或只 `continue` 而丢失信息。若某处必须继续执行，必须把失败原因
> 记录到返回值、日志或证据中，让调用方能够区分"成功"与"失败但被忽略"。特别注意：
> `finally` 中抛出 `SystemExit` 会替换正在传播的异常并吞掉 traceback —— 这类 `raise` 必须
> 先用 `sys.exc_info()` 守卫。禁止用 `except Exception: pass` 让"查询失败"伪装成"结果为空"。

### R6 — 边界条件：拒绝非法输入时不得留下副作用

- **Path patterns**：`src/Plugin.Shared/**`
- **Rule**：

> 交互式输入被拒绝（取消、错误、关键字、未知状态）时，命令必须**不创建任何图元**后返回。
> 只有明确表示"用户接受了输入或接受了默认值"的状态才允许继续执行写操作。对未知/未枚举的
> 状态必须 **fail closed**（拒绝），不得回退到默认值继续执行。几何参数（半径、长度等）
> 为 NaN、Infinity 或非正数时必须拒绝，且不得留下部分创建的实体。

### R7 — 资源释放必须真实上报

- **Path patterns**：`src/**`、`tools/**`
- **Rule**：

> 事务、锁、文件句柄、进程句柄、COM 对象都必须被释放，且释放结果必须**如实上报**：禁止在
> 释放失败时仍然返回 `disposed=true`。释放失败必须作为错误暴露给调用方。只读事务必须在不
> `Commit` 的情况下 `Dispose`（即中止），不得因为"看起来成功了"而提交。

### R8 — 并发与重入

- **Path patterns**：`src/**`、`tools/**`
- **Rule**：

> 共享可变状态（静态计数器、深度计数、注册表字典）必须有明确的并发策略。重入检测必须在
> **接触文档/数据库之前**完成并拒绝，而不是在访问之后再报告深度。递减/递增计数必须保证
> 异常路径下也能正确回退，不得让计数泄漏导致后续调用被永久拒绝。线程身份校验必须发生在
> 访问受保护资源之前。

### R9 — 回归测试必须能失败

- **Path patterns**：`tools/selftest.sh`、`tests/**`
- **Rule**：

> 断言必须在**父进程**中生效：禁止把断言写在生成的子进程/假可执行文件里，因为子进程里的
> 变量自增无法影响父进程的退出码（本项目曾因此把"安全闸已被移除"报告为 PASS）。
> 禁止用"文本包含某个字符串"来断言某个函数被调用 —— 注释也能满足它；应使用 AST 检查真实
> 调用。禁止依赖未导出的变量。新增的安全关键断言必须配套**变异测试**：故意破坏被测保护后，
> 套件必须失败。禁止重复的同名测试项。退出码必须取真实进程的退出码（注意管道的 `$?` 是
> 最后一条命令的退出码，必要时用 `PIPESTATUS` 或 `pipefail`）。

### R10 — 不得把"编译通过/自报成功"当作证据

- **Path patterns**：`**`
- **Rule**：

> 本项目的验收标准是"目标 DWG 的实际状态 / 真实停止位置 / 独立读回值"，不是工具返回的
> success，也不是编译成功。因此：不得新增"自报成功即通过"的断言；不得用 mock/fake 的结果
> 冒充真机结论；涉及真机的结论必须由**独立进程**读回验证。如果一处改动降低了验证强度
> （例如去掉读回检查），必须明确指出并说明理由。

---

## 5. Security Scan（基线全仓库扫描）

Security Scan 只扫描**默认分支**（`main`），所以它天然覆盖"现有旧代码"这一基线需求 ——
不需要也不应该制造一个巨大的伪 PR 去骗全量 Review。

1. 打开 <https://app.sourcery.ai/dashboard/security/repositories>
2. 在 `CadBridge` 行的 **Scanning enabled** 列启用。
3. 若计划支持按需扫描（Team 计划），可点 **Start Scan** 立即触发一次；
   Open Source 计划为每周两次（周一、周四），**没有**按需触发按钮。

扫描器（一次逻辑扫描，全部同时运行）：

| 扫描器 | 关注点 |
|---|---|
| Secrets | 已提交的凭据/token |
| SAST | 代码级漏洞 |
| IaC | Terraform/K8s 等配置错误（本仓库无此类文件） |
| Dependencies & licenses | 依赖漏洞与限制性许可证 |

> **限制要如实记录**：Open Source 计划下仪表盘最多显示 10 条发现。如果发现数被截断，
> 必须记录"可见发现数受计划限制"，不得把"只看到 10 条"报告为"只有 10 个问题"。

---

## 6. 日常流程（固定）

```
feature branch → commit → PR → Sourcery Review → 修复
              → Sourcery re-review → merge main
```

要点：

- 每次 push 会自动触发 re-review；**自动 re-review 上限为 5 次**，超过后状态检查显示
  `Skipped`。需要完整重跑时在 PR 里评论 `@sourcery-ai review`（会重置计数器）。
- 状态检查名为 **`Sourcery review`**。可将其加入分支保护规则；它**不会**阻塞合并
  （"Requiring it never blocks a merge"），所以"检查是绿的"**不等于**"评论已处理完"。
  合并前必须人工核对每条行内评论是否已解决或已说明不修改的理由。
- 可用命令：`review`、`summary`、`guide`、`title`、`resolve`、`dismiss`、`create issue`
  （`create issue` 只能在 Sourcery 的评论线程内回复使用）。也可用标签
  `sourcery-review` 等触发。
- 本项目约定：**不接受** Sourcery 自动 approve 代替人工确认（故 §2 中该项设为 Off）。

---

## 7. 配置回执（配置完成后填写）

> **重要**：下表是**待办清单**，不是完成记录。截至本文件最后更新，除第一行外**均未完成**。
> 在全部完成之前，Sourcery 接入状态是 **部分完成**，不得声称已完全接入。

| 项目 | 状态 | 证据 / 日期 |
|---|---|---|
| GitHub App 已产生本仓库 PR Review | ✅ 已完成 | PR #1 有 `sourcery-ai` 检查与中文评论 |
| App 安装范围＝仅 `moshouhot/CadBridge` | ⚠️ **无法用 API 验证**，需人工在设置页确认 | |
| Review profile = Verbose（基线期） | ☐ 待完成 | |
| Review language = 中文（显式设置） | ☐ 待完成（Review 已输出中文，但设置项未显式确认） | |
| Review rules R1–R10 已粘贴 | ☐ 待完成 | |
| Security Scan 已启用 | ☐ 待完成 | |
| 首次 Security Scan 结果已记录（含扫描 SHA） | ☐ 待完成 | |
| IDE「Review current file」已对核心文件执行 | ☐ 待完成 | |
| 基线整改 PR 已 merge 到 main | ☐ 待完成（PR #1 保持 open） | |

### 7.1 需要项目所有者执行的具体操作

1. **确认 App 安装范围**：<https://github.com/settings/installations> → Sourcery → 确认
   Repository access 为 **Only select repositories** 且仅勾选 `CadBridge`。
2. **Review Settings**：<https://app.sourcery.ai/dashboard/review-settings> → Review profile
   选 **Verbose**；**General** 标签页确认语言为中文；**Review rules** 标签页粘贴本文档 §4 的
   R1–R10。
3. **Security Scan**：<https://app.sourcery.ai/dashboard/security/repositories> → 为
   `CadBridge` 启用 **Scanning enabled**。若计划支持按需扫描则点 **Start Scan**，并记录
   被扫描的 commit SHA；若为 Open Source 计划（无按需按钮，每周两次），需等待或记录
   “尚未运行”。**同时记录仪表盘可见发现数是否被计划上限（10 条）截断。**
4. **IDE 审查现有核心文件**：在 VS Code / JetBrains 中打开本仓库，对下列文件逐个执行
   Sourcery 面板的 **Review current file**，并导出/截图发现：
   - `tools/safe_process.py`（进程归属与安全闸，最高风险）
   - `tools/dap-probe.py`、`tools/dap-session.py`（DAP 分帧与超时）
   - `tools/make-manifest.py`（证据持久化）
   - `tools/t01-5-repl-plugin-read.py`（真机编排）
   - `src/Plugin.Shared/DiagnosticsCommands.cs`、`src/Plugin.Shared/RadiusPromptPolicy.cs`
   - `src/Plugin.Shared/LiveReadLispFunction.cs`（并发/重入）
   - `src/Plugin.Shared/RuntimeInfo.cs`（防止常量冒充测量值）
5. **决定是否重写 `main` 历史**：`main` 仍含 2 处标识符（UTF-16 `.raw`，见
   `docs/evidence/PUBLICATION.md` §2.5b）。重写会移动 tag `baseline/pre-sourcery-audit`
   （审计回退目标），因此需项目所有者决定，本会话未自作主张执行。
