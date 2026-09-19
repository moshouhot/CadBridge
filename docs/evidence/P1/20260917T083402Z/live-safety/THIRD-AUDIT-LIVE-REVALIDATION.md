# 第三方审计：Live Revalidation（2026-09-18）

## 最终结论

**Live Safety Gate = BLOCKED**  
**P1 = INCOMPLETE**  
**T01-3 = 核心路径本轮 26/26 PASS，但总项仍未完全验收**  
**T01-5 = FAIL（本轮 29 PASS / 3 FAIL）**

本轮在项目所有者明确授权“独占、授权测试环境”后恢复了分阶段真机测试；但运行期间两次发现另一个 `TmAgent-ACAD`/`TmAgent-TEMP` 后台任务主动启动 AutoCAD 2023，因此独占条件事实上没有持续成立。CadBridge **没有终止、附加或修改这些外部进程**，并在发现后重新冻结全部 live harness。

---

## 1. Stage-1：Windows Job Object ownership smoke

目标：解决上一轮 blocker —— launcher/stub/child handoff 无法安全证明 ownership。

实现：

- `CreateProcess(..., CREATE_SUSPENDED)`；
- 在目标线程恢复之前 `AssignProcessToJobObject`；
- Job 设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`；
- 私有 registry token 保存 Job handle；
- cleanup 只调用 retained Job Object，不按 PID 名称扫描、不 `taskkill`；
- descendant 自动继承 Job，可证明属于本次启动的进程家族。

先用无害 Python fixture 验证：root + child 同属一个 Job，Job-only cleanup 同时收回两者。

AutoCAD 2026 smoke：

- primary PID：`24352`
- path：`D:\Program Files\Autodesk\AutoCAD 2026\acad.exe`
- creation：`2026-09-18T23:49:06.2734250+08:00`
- identity + Job membership 二次复核：PASS
- Job cleanup：PASS
- cleanup 后目标 AutoCAD 2026：0

证据：`ownership-smoke.json`

### 独占环境被外部任务打破

smoke 后全机检查发现 AutoCAD 2023 PID `18688`，但父链证明它**不是 Job 逃逸**：

```text
bash.exe 26308
  └─ SafeRunner.exe 20304
       F:\Nextcloud\project\逆向环境\TmAgent-ACAD\deliverable\tools\SafeRunner\SafeRunner.exe
       └─ acad.exe 18688
            D:\Program Files\Autodesk\AutoCAD_2023.1.5\AutoCAD 2023\acad.exe
```

父 bash 命令明确包含另一项目的：

```text
launch acad under SafeRunner (background)
```

因此结论是**并发第三方/另一 AI 任务**，不是 CadBridge Job ownership 失败。该链随后自然结束，CadBridge 未终止它。

由此补充安全规则：live harness 必须检查**全机所有 `acad.exe`**，不能只检查目标安装目录；启动后、进入 DAP/DB 操作前还必须二次复核，防止 preflight 之后出现外国实例。

---

## 2. Stage-2：T01-3 DAP attach / breakpoint / step / stack / variables

在确认全机 `acad.exe=0` 后，`dap-attach-a14.py` 改为 Job-owned host 并恢复单项 allowlist。

AutoCAD 2026 primary PID：`8868`。

结果：**26 passed / 0 failed**。

关键通过项：

- 全机独占在 DAP attach 前二次复核：PASS；
- DAP `initialize` / `initialized`：PASS；
- attach 到明确 PID：PASS；
- line 6 / line 8 两断点：`verified=true`；
- `(C:CBDBG)`：accepted；
- line 6 收到真实 `stopped(reason=breakpoint)`；
- stackTrace：真实 `C:CBDBG` 源帧；
- locals：`N=2`；
- Step In：进入 `CB-ADD`，`X=2`；
- Next：`Y=3`；
- Step Out：返回 `C:CBDBG`；
- Continue：命中 line 8；
- Job cleanup：PASS；
- cleanup 时 Job 内实际成员 8 个：`[3940, 8868, 12760, 12792, 16280, 20520, 22384, 25824]`；
- cleanup 后全机 `acad.exe=0`。

证据：

- `t01-3-attach.jsonl`
- `t01-3-attach.summary.json`

这证明 **T01-3 的核心正常路径**在新的 ownership 模型下仍成立。T01-3 总项仍未直接升级 PASS，因为原验收还要求异常策略、错误 PID/多实例行为、源码 hash 等子项。

---

## 3. Stage-3：T01-5 暂停态 managed `[LispFunction]` 只读 DB

### 3.1 首次运行：路径负例，安全失败

第一次命令将 `--workdir` 传成 Windows 反斜杠形式，外层命令解析后实际变成 drive-relative：

```text
F:CadBridge-run_t01-5-repl-audit\netload-baseline.scr
```

结果：

- baseline 文件未出现；
- DAP 未开始；
- harness FAIL；
- Job cleanup PASS；
- cleanup 后 `acad.exe=0`。

整改：`--workdir` 现在必须 `Path.is_absolute()`；adapter/acad/program/plugin-dll 均在 launch 前强制验证文件存在。

### 3.2 有效重跑：idle baseline + paused read 成功，但总项 FAIL

重跑使用绝对路径：`F:/CadBridge-run/_t01-5-repl-audit`。

AutoCAD 2026 primary PID：`27636`。

Idle command-context baseline（**DAP attach 之前**由 AutoCAD `CommandMethod` 执行）：

```text
CBBASELINE_RECORDED
native_thread_id=8024
managed_thread_id=1
is_application_context=false
has_document=true
```

暂停 line 8 后调用 managed `[LispFunction("CBLIVEREAD")]`：

```text
thread_id=8024
managed_tid=1
depth=1
context=verified
context_app=false
in_command=true
doc=Drawing1.dwg
lock_mode=not_requested_read_only
tx_ms=0
read_ms=6
entities=1
circles=1
last_circle_handle=2CE
last_circle_center=100,100,0
last_circle_radius=3
status=ok
tr_disposed=ok
lock_disposed=n/a_read_only
disposed=true
total_ms=19
```

并且：

- nonce 精确关联本次请求；
- DAP evaluate response 约 `0.051s`；
- 完整 plugin result envelope 在约 `0.101s` 内、**continue 之前**到达；
- measurement window 内没有 continued/terminated/exited/unexpected stopped/error/CBDBG_DONE；
- plugin read 后仍在同一源码、同一 line 8；
- idle baseline 与 paused callback 的 native thread 均为 `8024`、managed thread 均为 `1`；
- `IsApplicationContext=false`；
- 纯读路径没有显式 `DocumentLock()`；
- read transaction 正常 dispose。

这些是真实的、比上一轮更强的正证据：**暂停态 managed plugin read 本身已被实测证明可行**。

### 3.3 三个 mandatory failure

本轮最终：**29 passed / 3 failed**。

#### FAIL-1：独立 hover handle 交叉验证不可用

旧 probe 使用：

```text
evaluate context=hover + frameId=131
(cdr (assoc 5 (entget (entlast))))
```

真实 transcript 不是普通失败，而是 AutoCAD 2026 连续输出：

```text
0xC0000005 (Access Violation)
```

导致独立 handle 为 `UNAVAILABLE`。

#### FAIL-2：CBDBG_DONE 未捕获

`continue` 请求被 adapter 接受，但由于前面的 `hover+frameId` 已触发 Access Violation 并破坏调试会话，随后没有捕获 `CBDBG_DONE`。

因此 FAIL-1 与 FAIL-2 高度相关；不能据此把 `CBLIVEREAD` 本身判为错误。

整改（本轮已完成、但**未再真机运行**）：

- 永久禁止该交叉验证使用 `hover+frameId`；
- 改用 plain `hover`（无 frameId）读取 built-in expression；
- `0xC0000005` stdout 现在是强制 invalidation；
- 新增离线回归：Access Violation 输出必须使测量失败；
- 新增 mandatory：`independent hover cross-check did not invalidate paused session`。

#### FAIL-3：cleanup 后出现外部 AutoCAD 2023

CadBridge 自有 Job cleanup 成功，Job 内 8 个成员全部回收；但全机检查发现 PID `9932`：

```text
bash.exe 22332
  cmd: "F:/TmAgent-TEMP/probe_chain.sh"
  └─ acad.exe 9932
       D:\Program Files\Autodesk\AutoCAD_2023.1.5\AutoCAD 2023\acad.exe
       created=2026-09-18T23:56:05.3091940+08:00
```

这再次证明另一后台 TmAgent 测试在 CadBridge live window 内启动 CAD。CadBridge 没有终止 PID 9932。

证据：

- `t01-5-repl-rerun.json`
- `t01-5-repl-rerun.jsonl`

---

## 4. 本轮后续代码状态

已完成：

- Windows Job Object process-family ownership；
- 全机 `acad.exe` preflight，而不是仅目标目录；
- startup 后进入 DAP 前二次 exclusivity check；
- idle baseline 改为 DAP attach 前的真实 AutoCAD command context；
- `ExecutionContextBaseline` 同时验证 `IsApplicationContext` + native/managed thread；
- read-only `CBLIVEREAD` 不再显式 `DocumentLock()`；
- T01-5 输入路径必须绝对路径；
- dangerous `hover+frameId` cross-check 已删除；
- `0xC0000005` 纳入 invalidation；
- 所有 live harness 已重新从 allowlist 移除，恢复 default-deny。

最终离线回归：

- `safe_process.py --self-test`：**43/43 PASS**；
- `bounded_worker.py --self-test`：**6/6 PASS**；
- `test-repl-probe-offline.py`：**PASS**，新增 Access Violation 负例通过（相较上轮 32 项新增 1 项）；
- targeted pyflakes：**0 warning**；
- Legacy net48 build：**0 warning / 0 error**；
- Modern net8 build：**0 error**，仍保留原有 3 组 assembly-version warning（Microsoft.VisualBasic / System.Drawing / WindowsBase），不属于本轮 Safety Gate 根因。

---

## 5. 当前唯一可辩护的 Gate 结论

### 已真正闭环

1. Job Object ownership / process-family cleanup：**PASS**；
2. launcher/child family 不再靠全局 PID 扫描认领：**PASS**；
3. T01-3 核心 DAP 正常路径：**PASS 26/26**；
4. idle command-context baseline：**PASS（真机）**；
5. paused managed `[LispFunction]` 的只读 transaction + DB geometry read：**PASS（真机观测）**；
6. paused callback 与 idle baseline 的 thread/context 一致：**PASS（真机观测）**。

### 仍未闭环

1. `T01-5` 的独立 handle cross-check 修复后尚未重新真机验证；
2. `CBDBG_DONE` 需在不触发 Access Violation 的新 cross-check 下重新验证；
3. `DEBUG_BUSY` / `STALE_DEBUG_HANDLE` / control lease / 正式 stop_id 策略仍未实现；
4. 独占测试环境目前不可信：另一个 `TmAgent-ACAD` / `F:/TmAgent-TEMP/probe_chain.sh` 后台任务两次在测试窗口内启动真实 CAD；
5. T01-3 总项仍缺异常策略、错误 PID/多实例、源码 hash 等计划子项。

因此：

```text
P1 = INCOMPLETE
Live Safety Gate = BLOCKED
G01 = NOT_RUN
G02 = NOT_RUN
```

在外部 TmAgent live 测试任务停止之前，不应再次 allowlist CadBridge live harness。
