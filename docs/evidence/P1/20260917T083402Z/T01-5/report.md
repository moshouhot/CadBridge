# P1 / T01-5 — 暂停点图面 live query 与防死锁（核心不可降级项）

**结论：核心机制有可行性证据，但本项记 NOT_RUN，不得判 PASS。**
在 AutoCAD 2026 上，经 DAP `evaluate` context=`repl` 调用**插件自身的**
managed 回调（`[LispFunction("CBLIVEREAD")]`），其返回字符串经 DAP **output 事件**
在客户端发出 `continue` **之前**到达（实测 0.0s）。证据：`repl-plugin-read.json`。

**本轮证据的限度（审查后收窄）**：该证据**未**证明——stop 世代未变、期间无内部
resume/再停止、回调线程身份（除插件自报外）、可安全重入。
成功取得锁与事务本身**不**证明上述性质。

**仍未完成**：`DEBUG_BUSY`、`STALE_DEBUG_HANDLE`、控制租约等策略层要求**尚未实现**（见 §5）；
且**安全审查未通过**，已暂停新的真机实验（见 `REVIEW-REMEDIATION-round2.md`）。
因此 **G02 不因本项通过**。

---

## 1. 本机已验证的可用组合（核心发现）

| 要素 | 取值 | 说明 |
|---|---|---|
| 回调机制 | managed `[LispFunction("CBLIVEREAD")]` | **插件代码**，不是普通 Lisp |
| DAP context | **`repl`** | **只有 `repl` 会执行用户自定义函数** |
| 取值通道 | **stdout** | `repl` 的响应 `result` 字段**恒为空** |
| 送达时机 | 暂停期间（实测 0.0s） | **不是**「先 continue 再查」 |

实测返回：

```
"CBLIVEREAD_OK entities=1 circles=1 last_circle=handle=2CE center=100,100,0 r=3
 src=CadBridge.Plugin.Shared.LiveReadLispFunction"
```

内嵌标记 `src=CadBridge.Plugin.Shared.LiveReadLispFunction` 证明该值来自**插件代码**，
而非普通 Lisp 表达式（例如 `(entget (entlast))`）。

## 2. 产品级硬限制（权威消息，已逐码点解码）

以 `hover` / `watch` / `clipboard` 求值**用户自定义函数**时，适配器直接拒绝：

```
无法在监视窗口中计算用户定义的函数
（codepoints 0x65e0 0x6cd5 0x5728 0x76d1 0x89c6 0x7a97 0x53e3 ...）
"Cannot evaluate a user-defined function in the watch window."
```

因此两条通道的边界是**产品设计**决定的，不是实现选择：

| context | 能否调用用户/插件函数 | 值在哪里返回 |
|---|---|---|
| `hover` | ❌ **本函数在本上下文**被拒（内置函数如 `entget` 可用） | 响应 `result` 字段 |
| `watch` / `clipboard` | **未单独验证** | — |
| **`repl`** | ✅ 用户函数 + 插件函数（`(C:CBDBG)` 本身即如此启动） | **stdout**（本机观测 `result` 为空） |

**限度**：`hover` 的拒绝只测了**该函数**；不得推广为「所有上下文」或「所有用户函数」。
`watch`/`clipboard` 对用户函数的行为**未测**。`repl` 的 `result` 为空是**本机观测**，
不是普遍保证。

**限度（审查后收窄）**：上述只测了**该函数**在 **`hover`** 下的行为，
因此「插件函数 + hover」对该函数不成立；**不得**推广为「所有上下文」或「所有用户函数」，
也**不得**断言 `repl` 是**唯一**可能的插件通道（`watch`/`clipboard` 对用户函数未测）。
这解释了早前「plain-Lisp 读得到、plugin 读不到」的差异——差异正是**插件/非插件边界**。

## 3. 与更早观测的关系（含一处更正）

| 早前观测 | 现状 |
|---|---|
| 暂停期 `hover` 可读 `(entget (entlast))` → `3.0` | 成立，但**仅限内置函数**；用户函数被拒 |
| 暂停期 COM 读取 35–45s 未返回，continue 后立即成功 | 成立。这是**关联观测**；它**不能**证明所有插件路径都被阻塞——§1 的插件路径就是通的 |
| 暂停期 `repl` 响应 `result` 为空 | 成立，但**当时把它解释为「排队注入」是错的**（见下） |

**更正**：早前报告把 `repl` 描述为「排队注入，值只在 continue 后才出现」。
**该结论错误**——早前脚本只是在**事后**去收集累积的 stdout。
本项用**计时**测量：值在响应后 **0.0s** 即出现在 stdout，且此时目标**仍在暂停**，
未发出任何 `continue`。

## 4. 防死锁与陈旧性

| 检查 | 结果 |
|---|---|
| 插件读**在 continue 之前**送达 | ✅ `marker found while still paused: True (after 0.0s)` |
| 插件读报告 `circles=1`、`r=3` | ✅ |
| 暂停未被卡死：`continue` 成功 | ✅ |
| continue 后 `CBDBG_DONE` 输出 | ✅ |
| 独立 COM 旁证（continue 后） | ✅ 读到 `r=3` 的圆 |
| 陈旧性 | **本轮无有效证据**。早前报告曾称「受保护表达式在两停止点返回 `-1` / `1`」，但该改动是在那次运行**之后**才写入脚本的，**从未实际运行**，故**撤回**。脚本现已具备该逻辑（已知基线 + 精确比较），但**尚无运行结果**。 |

## 5. 尚未完成（不得据本项推断已通过）

| 要求 | 状态 | 归属 |
|---|---|---|
| `DEBUG_BUSY`（暂停期普通写被拒） | **NOT_IMPLEMENTED** | 适配器**不拒绝**暂停期写入（实测 `entmakex` 生效）→ 必须由 CadBridge Host/调度器强制。P2 合同 + P3 调度器 |
| `STALE_DEBUG_HANDLE`（过期 stop_id 被拒） | NOT_IMPLEMENTED | 帧句柄 continue 后仍可解析 → 须由 Host 基于 stop_id 判定。P2/P5 |
| 非授权控制被拒 / 控制租约 | NOT_IMPLEMENTED | P5/T05-4 |
| `DEBUG_QUERY_UNSAFE` 安全退化 | NOT_OBSERVED | 本机未出现不安全中间状态；阈值判定属 P2/P5 |
| 同一 stop_id 的正式语义 | **NOT_RUN** | 本轮用 frame id 定位；**frame/thread id 不等于 stop_id**，正式 stop_id 需在 P2/P5 建立 |
| 期间**无内部 resume / 再停止** | **NOT_PROVEN** | 现有证据只表明「客户端未发 continue」；调试器内部行为未观测 |
| 回调线程身份 | **仅插件自报** | 未与宿主报告的命令上下文线程对照 |
| 可安全重入 | **NOT_PROVEN** | 需专门实验 |
| 2022 / 2024 宿主复现 | NOT_RUN | T01-6 |
| 多 Agent 冲突 | NOT_RUN | P5/T05-4 |

**本项负责的是「暂停期经插件合法读取真实、非陈旧、无死锁」这一条**，
不负责上述策略层要求。

## 6. 对后续阶段的设计输入

1. **暂停期查询应走 `repl` + 插件 `LispFunction`**（本机唯一已验证可行的形态）；
   `hover` 对本插件函数被拒。`watch`/`clipboard` 对用户函数未测，不应视为已排除。
2. **必须把「响应 `result` 为空 + 值在 stdout」建模为一等语义**，否则会实现出假同步。
   → P2 合同需要「提交 / 完成」两个明确回执（与 T01-4 同源）。
3. **写入授权必须由 Host 强制**：适配器在暂停期不拒绝写入。
4. **`watch` + `frameId` 组合暂不使用**（一次混合实验中其后出现 `0xC0000005`；
   **因果未确立**，且该会话此后的成功**不能**反证其安全性）。
5. **帧/线程 id ≠ stop_id**：过期句柄判定必须由 Host 侧建立，不能依赖适配器报错。
6. **绝不在后台线程访问数据库**：`LispFunction` 在调用线程上运行，这一点必须保持。

## 7. 本轮暴露并已修正的工具缺陷

| 缺陷 | 后果 | 处置 |
|---|---|---|
| 脚本硬编码 `"ACHIEVABLE"` 判定 | 断言全失败也会打印成功 | 改为**由检查结果派生**，并列出未主张项 |
| 断言检查不存在的 `queued` 字段 | 假通过 | 改为检查「值是否在响应体/stdout 中真实出现」 |
| 陈旧性用「错误串 vs 值」比较 | 错误串不是受控基线 | 脚本改为**受保护表达式 + 已知基线**（无圆返回 `-1`）；**但该改动尚未运行**，故不作测量声明 |
| 失败时仍返回 0 | 失败被当成功 | 有失败即**非零退出** |
| `win32com.client.Dispatch` 回退 | 可能启动/选中非预期实例 | **移除**，只允许 `GetActiveObject` |
| 按进程名清理（`Stop-Process -Force`） | 可能关闭用户 CAD | 见 `SAFETY-INCIDENT-process-cleanup.md`；改用 `safe_process.terminate_owned`（PID + 创建时间 + 可执行路径） |

## 8. 证据链

| 文件 | 作用 |
|---|---|
| `pause-query.json` | 首次观测：暂停期 COM 未返回；continue 后可读 |
| `pause-live-query.json` | 复现 COM 阻塞；`repl` 返回空 `result` |
| `sync-read-forms.json` | `hover`/`watch`/`clipboard` 返回内置表达式的真实值；记录 `watch+frameId` 之后的访问冲突 |
| `definitive.json` | 两停止点对比 + 防死锁（25/25） |
| `plugin-pause-read.json` | **插件函数 + hover 被产品拒绝**（权威中文消息） |
| **`repl-plugin-read.json` / `.jsonl`** | **决定性证据：插件函数 + repl 在暂停期成功读取（14/14）** |

工具：`tools/t01-5-{pause-query-probe,pause-live-query,sync-read-forms,definitive,plugin-pause-read,repl-plugin-read}.py`
插件探针：`src/Plugin.Shared/LiveReadLispFunction.cs`

## 9. 安全与现场

- 见 `SAFETY-INCIDENT-process-cleanup.md`：早期清理方式**不当**，已自纠并加入自动化检查
  （`tools/selftest.sh` 现会拒绝按名终止与 `Dispatch` 回退）。
- 仅终止**本脚本启动并登记**的 PID；清理前重新校验 PID + 创建时间 + 可执行路径。
- 未改注册表、未改 `SECURELOAD`/`TRUSTEDPATHS`/`LISPSYS`、未写任何用户 DWG。
- 设置了 `ACAD_USERPROFILE`；**其隔离效果未经证实**，仅记录为「已设置」。
- 插件探针为**只读**（`OpenMode.ForRead`，无 `Commit`，事务析构即中止）。

## 10. 许可证

本机所有 AutoCAD 宿主 `license_provenance=unverified`，本项结果标记 `LICENSE_UNVERIFIED`，
在项目所有者确认前不得作为 Gate 放行证据。
