# CadBridge 依赖与选型锁定（T00-2）

版本：1.0 · 日期：2026-09-17 · 证据：`docs/evidence/P0/20260917T083402Z/raw/source-register*.{json,md}`

> 本文件记录**已锁定**与**仍未锁定**的依赖。未锁定项明确标注，不假装已完成。
> 所有许可结论来自实际读取包内 `LICENSE`/`.nuspec`，不是从包名或仓库首页推断。

## 1. 运行时与目标框架（已锁定）

| 项 | 决定 | 依据 | 状态 |
|---|---|---|---|
| Host TFM | `net10.0`，自包含 win-x64 | Host 不受 CAD 进程约束；.NET 8 支持止于 2026-11-10；本机 SDK 10.0.400 | **LOCKED** |
| Plugin Legacy | `net48` | 2024 及以前宿主无 `acdbmgd.runtimeconfig.json`、仅有 `acad.exe.config`（.NET Framework） | **LOCKED** |
| Plugin Modern | `net8.0-windows` | 2025/2026 宿主 `acdbmgd.runtimeconfig.json` 声明 `tfm: net8.0` | **LOCKED** |
| Legacy 参考程序集 | `ObjectARX-2014\inc`（assembly **19.1**） | 本机**最旧**可用的 Autodesk 托管参考集，作为保守下限 | **LOCKED** |
| Modern 参考程序集 | `ObjectARX-2025\inc`（assembly **25.0**） | 本机最低 Modern 代（首个 .NET 8 AutoCAD） | **LOCKED** |
| 参考集 | `core` = acdbmgd + accoremgd | 同一共享源需同时可在完整宿主与 `accoreconsole` 中加载 | **LOCKED** |

**关于 Legacy 下限的重要限制**：AutoCAD 2015 是 **R20.0**，本机**没有** R20.0 参考程序集。
因此以 19.1 编译**不能**证明 2015 兼容性，2015 属**结构性未验证**，在兼容矩阵中如实标注。
（本轮曾误用 2024 宿主目录作参考；已改为锁定最旧代。）

**年份映射（修正）**：R24.0–R24.3 = 2021–2024；R25.0 = 2025；R25.1 = 2026。
上一版把 "25.x" 概括为 2022–2026 是**错误的**。

## 2. NuGet 依赖（逐项锁定）

| 包 | 版本 | 许可（包内实际） | 目标框架 | 决定 |
|---|---|---|---|---|
| `ModelContextProtocol` | 2.2.0 | Apache-2.0 | netstandard2.0 / net8.0 / net9.0 / net10.0 | **LOCKED**（Host 侧） |
| `ModelContextProtocol.AspNetCore` | 2.2.0 | Apache-2.0 | net8.0 / net9.0 / net10.0 | **LOCKED**（Host 侧） |
| `Microsoft.Extensions.Logging` | 10.0.12 | MIT | net462 / netstandard2.0 / net8.0 / net10.0 | **LOCKED** |
| `System.Text.Json` | 10.0.12 | MIT | net462 / netstandard2.0 / net8.0 / net10.0 | **LOCKED** |
| `Newtonsoft.Json` | 13.0.4 | MIT | net20…netstandard2.0 | **LOCKED**（NJsonSchema 传递依赖） |
| `NJsonSchema` | 11.6.1 | **MIT** | **net462** / netstandard2.0 / net8.0 | **LOCKED**（Schema 校验） |
| `Lucene.Net` | 4.8.0-beta00018 | 包内 LICENSE.txt（Apache-2.0 系） | net462 / netstandard2.0 / net8.0 | **CANDIDATE**（本地检索） |
| `Lucene.Net.Analysis.Common` | 4.8.0-beta00018 | 同上 | 同上 | **CANDIDATE** |
| `Lucene.Net.QueryParser` | 4.8.0-beta00018 | 同上 | 同上 | **CANDIDATE** |

### 2.1 Schema 校验库选择理由

候选对比（均实际下载 `.nupkg` 读取）：

| 候选 | 许可 | TFM | 结论 |
|---|---|---|---|
| `JsonSchema.Net` 9.4.0 | **OSMFEULA**（Open Source Maintenance Fee） | netstandard2.0 / net8.0 / net9.0 | **不采用** |
| `NJsonSchema` 11.6.1 | **MIT** | **net462** / netstandard2.0 / net8.0 | **采用** |

`JsonSchema.Net` 源码为 MIT，但**二进制发行**（即 NuGet 包）附带 OSMF EULA：
年毛收入 ≥ 10,000 美元的营利性使用者需支付维护费。CadBridge 需要清晰的再分发条件，
且该库**不提供 net462**（Legacy net48 插件无法直接消费）。故选择 `NJsonSchema`：
MIT、含 `net462` 资产（可被 net48 消费）、依赖链均为 MIT。

**这不是"必须复用 Python"的替代**：DESIGN D03 要求单一 Schema 来源，NJsonSchema 仅承担
Host 侧完整校验；Plugin 侧使用同一合同生成的轻量 DTO 与约束，两端共用同一契约版本。

### 2.2 本地检索库（仍未最终锁定）

Lucene.NET 全系**没有稳定版**，最新为 `4.8.0-beta00018`。必须**显式锁定**该版本：

- `Lucene.Net.Analysis.Common 4.9.0` 已被标记 **deprecated / unlisted / net451-only**，且是
  第三方重编译（原因标记 `Legacy` + `CriticalBugs`）。任何"取最新版"的解析都会选到它。

**未锁定原因**：中文检索需要分词方案。`Lucene.Net.Analysis.Common` 自带的是英文/通用分析器，
**不含中文分词器**；需另行评估（如 `Lucene.Net.Analysis.Cn` 或自建同义词+字符切分）。
在中文分词方案与许可确认前，本项保持 **CANDIDATE**，不进入发布包。

## 3. Autodesk 程序集：许可与分发边界

| 事实 | 来源 | 影响 |
|---|---|---|
| `AutoCAD.NET` NuGet 包的 `.nuspec` 列出 `owners = Autodesk, Inc.`，包内 `LICENSE.txt` 为 ObjectARX EULA | 实际下载 24.3.0 / 25.1.1 / 26.0.0 包检查 | 用于**开发**；**不随包分发** |
| Autodesk 自带 `tools/install.ps1` 对全部托管程序集设 `CopyLocal=false` | 包内文件 | 厂商本身不打算让这些 DLL 随产品分发 |
| 本机 `D:\CAD APPLOAD\ObjectARX\ObjectARX-*` 六个 SDK 树中**不存在**任何 `LICENSE*/NOTICE*/EULA*/Redistrib*.txt` | 全树扫描 | 本地 SDK 渠道**没有**随附再分发文本；其许可来自同一 EULA |

**CadBridge 规则（已实现为构建期强制）**：
Autodesk 托管程序集仅作 `HintPath` 编译引用且 `Private=false`；构建目标在
`ReferenceCopyLocalPaths` 中出现 `acdbmgd/accoremgd/acmgd` 时**直接报错**。
发布包**不含**任何 Autodesk DLL，运行时从已安装的 AutoCAD 解析。

**未证实事项（不得当作已确认）**：
- `owners` 字段**不是**发布者身份的独立证明。NuGet 实际发布账户与官方渠道的对应关系**未独立核验**。
- 是否存在书面的 Autodesk 再分发许可**未验证**。但按上述规则（不分发 Autodesk 二进制），
  这不构成发布阻断项；**不应**凭空增加一条"必须取得再分发授权"的 Gate。

## 4. 上游来源（复用边界）

| ID | 仓库 | HEAD | 许可 | 复用方式 |
|---|---|---|---|---|
| S03/S04 | `Autodesk-AutoCAD/AutoLispExt` | `74f59ee220a3` | Apache-2.0（LICENSE.md） | **只读研究**接入信息；**不**捆绑厂商 adapter 二进制 |
| S07 | `modelcontextprotocol/csharp-sdk` | `324ccd83c357` | NOASSERTION（Apache-2.0 + MIT + CC-BY-4.0 附录） | 直接依赖官方包 |
| S09 | `moshouhot/batchPrintZWCAD` | `6ded809582f9` | MIT | **仅参考**双 TFM 构建方法；不复制业务代码 |
| S10 | `U-C4N/Autocad-MCP` | `abc2a82e7128` | MIT | 借词典/排名思路 |
| S11 | `mcp-scout/mcp-scout` | `76b28888dc85` | MIT | 借 search/describe/error 合同思路；V1 不引入 Node |
| S13 | `beiming183-cloud/AutoCAD-MCP` | `11f7c47e5038` | MIT | 仅机制参考 |
| S14 | `bimwright/dwg-mcp` | `c04af1ff5ae3` | Apache-2.0 | 结构对照 |

DESIGN D17 记录的 **8 个 blob SHA 在审计时仍全部可复现**，无来源漂移。

### 4.1 中文发现词典（重要缺口）

`U-C4N/Autocad-MCP` 的 `discovery/aliases.py`（MIT，blob `f1f0020bd0a8c2335c441b223b73023647ac640b`）
是**纯英文**语料：对全部 199 个受跟踪文件做 CJK 扫描（U+4E00–U+9FFF）**命中 0 处**。
例如 FILLET 条目仅有 `("fillet","round the corner","rounded corner","radius the corner","tangent arc corner")`。

→ **CadBridge 必须自建中文别名词典**（R04/A04/A24 要求中文发现）。该文件也**未**登记在
DESIGN D17（D17 只登记了 `serialize.py`）。这是一个需要在 P4 前补齐的实质工作项，
不能靠"复用上游词典"解决。

## 5. TFM 兼容性（修正一处错误推论）

上一版曾据"没有 net48 资产"推出插件不兼容，这是**错误的**：
`net48` 可以消费兼容的 `netstandard2.0` / `net462` 资产。

正确表述：

| 事实 | 正确含义 |
|---|---|
| `ModelContextProtocol` / `.AspNetCore` 无 .NET Framework 资产（netstandard2.0 起） | net48 **插件**不应引入 MCP SDK。这是**设计边界**（DESIGN D02：不给 net48 插件引入完整 ASP.NET Core/MCP SDK），**不是**"net48 无法使用 netstandard2.0 库"。 |
| `Microsoft.Extensions.Logging` / `System.Text.Json` 提供 `net462` 资产 | net48 插件**可以**使用；`net462` 资产可被 `net48` 消费。 |
| `NJsonSchema` 提供 `net462` | 如将来需要，net48 侧可消费。 |

## 6. 仍未锁定（T00-2 未完成的原因）

| 项 | 状态 | 阻塞 |
|---|---|---|
| 本地检索库最终选型（含**中文分词**方案与许可） | **CANDIDATE** | P4 检索实现前必须解决 |
| 日志文件 provider（Microsoft.Extensions.Logging + 滚动文件 sink） | **未锁定** | 需确认滚动/大小上限与许可 |
| CLI 参数解析库 | **未锁定** | 可用标准库，需记录决定 |
| DAP 客户端库 | **未锁定** | 需在 T01-3 先确认真实协议行为再选库 |

因此 **T00-2 保持 BLOCKED**，P0 不能判 PASS。
