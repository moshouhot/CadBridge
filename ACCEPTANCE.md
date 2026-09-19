# CadBridge 验收规范与文档交付检查

版本：1.0 · 2026-09-17 · 需求来源：[PRD.md](PRD.md)，技术合同：[DESIGN.md](DESIGN.md)，阶段：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

**本轮只交付文档。下面的 CLI/API、Fixture、测试工具和证据路径是后续实施要求，不是已经存在或已经运行的程序。所有产品测试初始 NOT_RUN。** 文档静态核验与产品真机验收分别记录。

## 1. 状态与证据规则

状态：PASS（有证据满足预期）、FAIL（有反例）、BLOCKED（缺环境或关键前提）、NOT_RUN（未执行）、N/A（经批准明确不适用）。skip、编译成功、Fake通过、工具自报success均不能替代真实CAD通过。

每次run保存：源码commit或目录hash、四文档版本、构建hash、依赖锁、OS、.NET/Framework、AutoCAD完整build/update/语言包、Plugin/Adapter版本、实例与文档身份、fixture源码/hash、请求响应、退出码、相关日志、独立读回及人工/AI审核结论。敏感凭据脱敏，用户DWG/源码不默认上传。

证据按 `docs/evidence/<phase>/<run_id>/` 管理；manifest逐测试记录expected/actual/status/artifact paths。文件不存在、结果被截断而关键部分没读到、模拟结果替代真机时均不能PASS。

## 2. 固定Fixture与独立Oracle

### F-CAD：二维基础图

在一次性 `cb-fixture.dwg` 创建并保存受控基线，INSUNITS=4（毫米），UCS=WCS。建立WALL、TEST、0图层；模型空间包含：

- WALL上线段L1：(0,0,0)→(100,0,0)；圆C1：(100,100,0)、r=10；圆C2：(200,100,0)、r=600。
- 文字T1“污水泵房”；90度圆弧、闭合矩形轻量多段线、独立定义的SPLINE、面积200的简单SOLID HATCH。
- 含属性“编号=A001”的块实例，以及有旋转/缩放的嵌套块实例；Layout1另放独立图元，确保不会混入Model查询。
- 另备只读自定义/Proxy对象fixture；没有对应合法样本时该子项BLOCKED，不能伪造自定义对象通过。

基线由独立测试辅助插件或人工按说明创建，记录精确handles和期望数据。Oracle不复用待测Handler的计算/序列化逻辑；DWG重新打开核查与独立Autodesk API读取至少一种参与关键写入验证。

F-PAGE单独生成501条可区分LINE；F-LARGE单独生成50,000个实体做预算测试，不让每个小测试都加载大图。

### F-LSP：断点与图面联动

将下列代码原样保存为 `cb_debug_fixture.lsp`，LF/UTF-8，第一行即defun；记录SHA-256。数字行号按下列代码块正文计算，不把Markdown围栏计入。

```lisp
(defun cb-add (x / y)
  (setq y (+ x 1))
  y)
(defun c:CBDBG (/ n p)
  (setq n 2)
  (setq p (cb-add n))
  (entmakex (list '(0 . "CIRCLE") '(10 100.0 100.0 0.0) (cons 40 p)))
  (princ "CBDBG_DONE")
  (princ))
```

第6行进入cb-add；执行第2行后x=2、y=3；回到调用者p=3；第8行执行前应已经生成r=3圆。调试器若把断点绑定到相邻可执行位置，应返回verified实际位置并证明语义等价，不捏造请求行已命中。

F-ERROR：

```lisp
(defun cb-bad (x)
  (+ x 1))
(defun c:CBFAIL ()
  (cb-bad "bad")
  (princ))
```

用于Break on Error和栈检查。错误文本可本地化，但必须来自真实错误；不能自行补齐栈帧/行号。F-ENC为中文路径、空格、UTF-8/MBCS及无法无损编码样本。F-RELOAD包含v1定义而v2删除的函数，检验reload不等于卸载的行为与说明。

## 3. A01 — Portable与无永久修改（R01）

步骤：在可写中文/空格路径解压绿色包；比较启动前后CAD永久启动项、TRUSTEDPATHS、SECURELOAD、LISPSYS和CadBridge自有文件位置；显式attach，退出Bridge并由测试人员结束一次性CAD会话；删除绿色目录。

预期：不创建默认.bundle/MSI/Windows Service/开机项；不降低SECURELOAD；自有配置/日志/运行数据在声明root；系统/CAD自有文件单独列明。目录不可写明确失败，不偷偷回退AppData。旧CAD会话仍加载的程序集不能被声明“已卸载”。

证据：文件/配置前后清单、命令日志、相关设置读回、删除/被锁文件的实际结果。非受信任路径被策略阻挡是受限环境测试通过，不等于成功加载用例通过；需另有授权环境中的成功链路。

## 4. A02 — 精确选择CAD与临时加载（R02）

步骤：同版本同时打开两个CAD A/B，另有不同版本C；`discover`记录PID/start_time/path；`attach --pid <B>`。检查NETLOAD接收方与Plugin hello；再次attach；显式launch未运行版本；测试无效PID/权限不同/COM对象误匹配。

预期：只给B加载，A/C图纸和窗口状态不受影响；目标无法证明时ATTACH_AMBIGUOUS，不新建隐藏进程；重复加载复用同版插件；已加载旧版提示RESTART_REQUIRED而不强盖DLL。不存在自动保存、强制关图或盲目键盘fallback。

证据：所有进程前后清单、HWND→PID证明、插件身份、错误/成功响应、独立检查。

## 5. A03 — 会话/文档/协议/能力（R03）

步骤：打开相同文件名的两个文档；记录身份；关闭重开同路径；重启Host但不重启Plugin；模拟PID复用、旧descriptor和协议无交集；分别报告可用/不可用/未验证Debugger。

预期：document_id不按文件名复用；失效target返回SESSION_EXPIRED或DOCUMENT_CLOSED；Host恢复前重新握手，不能自动切换其他实例；协议取兼容交集；debug未测不为true；后续写入绑定实际目标。

另测无活动文档时document.create/open：只凭已验证实例身份成功创建新document_id，不要求调用者编造旧文档或Revision；返回后普通写操作必须使用新身份及其实际Revision。CLI仅给PID时仍须补全并核对启动时间。

证据：hello/status、身份迁移记录、Fake/真实进程用例分类。Fake不能独立证明实际进程识别。

## 6. A04 — 七工具、Discovery与Schema（R04）

步骤：读取默认和lisp-dev的tools/list；后端分别注册100/200/500条合成能力；检索“圆角/FILLET/round corner”等正例及未实现能力负例；describe简单/复杂参数；精确调用已隐藏但未授权的name；通过batch/meta嵌套尝试绕过。

预期：默认恰好7个约定工具，lisp-dev恰好10个；序列稳定；隐藏目录不进入通用Schema全量枚举；实际不可实现能力不假装可用；调用仍按具体Schema和权限校验。参数错返回field_path/expected/signature，不能要求靠盲猜试错发现参数。

证据：原始tools/list JSON、完整Schema、负例响应、检索goldens与A24 Token统计。

## 7. A05 — MCP/REST/CLI合同一致（R05）

对相同target和逻辑operation分别从三个入口执行：正常query、非法半径、未知字段、错误枚举、权限不足、同幂等key写入。传输层request_id可不同，但业务输入规范化相同。

预期：业务值/错误code/field_path/副作用一致；HTTP status、CLI exitcode和MCP isError符合设计映射；未知字段不被一条入口悄悄忽略。相同写key不因换入口而重复。

证据：三组规范化对照、schema hash、实际副作用。CLI不得绕过Host直连Plugin。

## 8. A06 — 真实结构化感知与分页（R06）

对F-CAD：按WALL+CIRCLE得到C1/C2；按文字包含“污水”得到T1；Model不得返回Layout1图元；selection结果与人工选择一致；各核心类型取真实几何/属性；块展开有变换与instance_path；未知bbox/Proxy显式null或unsupported。

对F-PAGE：limit=100全程遍历，去重后501项且与独立Oracle相等。翻页前人工修改，旧cursor必须CURSOR_STALE，不能无提示拼接。对F-LARGE：默认query不超过100项/64KiB文本，返回分页/截断信息；fields仅取handle+bbox时不夹带全几何。

region的bbox模式必须声明候选而非精确相交；构造bbox相交但几何不相交样本，不能误报“精确命中”。INSUNITS改为无单位时不得返回“单位=mm”。

证据：原图、Oracle、查询请求/分页合并结果、截断与单位说明。

## 9. A07 — 视图与图像证据（R07）

步骤：正常/最小化/遮挡窗口分别capture；对已知实体zoom；用附件API获取图像并验证SHA-256和格式；图像产生后切图再查询旧附件。

预期：能获得真实指定CAD图像或明确受限错误，不把其他窗口当CAD；图像target/time/revision明确，历史附件标历史；默认不抢焦点；附件大小受限、认证有效。zoom改变视图不能报告数据库atomic提交。

证据：PNG及metadata、遮挡对照、附件hash、权限负例与窗口状态。

## 10. A08 — 最小编辑集与读回（R08）

按DESIGN D03/D18逐项创建线/圆/多段线/文字/图层，插入已有块并修改属性，移动/旋转/缩放/换层/删除。使用独立Oracle比较坐标、半径、文字、图层和属性；几何误差默认绝对1e-8 drawing units，采用更宽容差必须说明算法/样本并评审。

负例：不存在layer/block/handle、负/零radius、非有限坐标、非平面轻量多段线、锁定层禁止写、错误document；拒绝后不得遗留半创建实体。返回handle只能引用本次真正存在且已提交对象。

证据：输入、返回、独立数据库读回；保存副本后重新打开至少一次。不得仅对照同一Handler的计算结果。

## 11. A09 — Atomic Batch（R09）

正例：D08的建圆→引用s1.handle移动，默认atomic；最终仅一个圆，中心(110,100,0)、r=50。所有Handler共用批Transaction。

运行中失败例：s1创建圆，s2移动符合格式但不存在的handle，s3创建文字。s1应rolled_back，s2 failed，s3 skipped，committed=false；独立查询确认原有对象属性/数量未改、无新增圆/文字。

预检失败例：atomic中放document.save或lisp.eval；在任何数据库修改前NON_TRANSACTIONAL_COMMAND。另测非法参数、向后引用/循环引用、引用不可公开字段。

回滚证据基于数据库语义，不要求DWG二进制hash、Handle分配器或Revision数字完全不变；不得把已回滚的handle返回为可用对象。

## 12. A10 — Best-effort（R10）

步骤：独立s1创建圆成功，s2非法handle失败，s3独立创建文字成功，s4依赖s2失败结果。mode显式best_effort。

预期：status=partial，ok=false，committed=null；s1/s3已提交，s2 failed，s4 skipped_dependency；s2内部无半完成效果。超预算未执行步骤明确skipped_budget。副作用batchable=false操作在多步批中执行前拒绝。

证据：逐步结果和实际对象，不能只报告success_count。

## 13. A11 — 外部修改与Revision（R11）

记录revA后，分别人工编辑、运行外部LSP、修改块属性、Undo、Redo；每次用旧revA请求写入。另构造Host入队后、Plugin实际执行前人工修改的竞态。关闭重开同路径验证document代次。

预期：所有受支持修改使旧观察失效；执行期检查返回REVISION_CONFLICT，不落下写入。事件丢失/追踪失效时REVISION_UNTRUSTED。回滚允许保守增加Revision，但不能漏检外部变化。不能用只增Bridge计数器通过本项。

证据：事件/队列时序、revision前后、独立实际数据库。对业务过滤不相关的修改也至少采用保守失效，不需要实现复杂语义合并。

## 14. A12 — 幂等、断线与未知结果（R12）

同key同payload顺序/并发提交10次；接受后丢弃一次响应并重连；Host重启但Plugin存活；同key更换radius；超过保留容量；在可控一次性测试进程里模拟CAD在Commit附近异常退出。

预期：活会话内只有一次实际效果，重复请求返回同job/原结果；异payload为IDEMPOTENCY_CONFLICT；保留策略不能静默驱逐旧key后重复执行。崩溃无法证明结果时OUTCOME_UNKNOWN且自动写锁止，不报告肯定失败、肯定回滚或exactly-once。

证据：key/digest/Plugin登记、Host重连、对象数、故障时间点、恢复判断。故障测试只针对测试人员授权的临时CAD，不杀用户会话。

## 15. A13 — Lisp基础运行与编码（R13）

执行：load F-LSP、eval `(+ 1 2)`、call cb-add(2)、run C:CBDBG；返回数值3、实际r=3圆及CBDBG_DONE输出（注明来源/coverage）。nil/T/list/string结果保持类型。错误输入返回真实错误，无源位置时line=null而非猜值。

F-RELOAD证明旧定义/全局状态未自动卸载并有明确说明；F-ENC覆盖中文/空格路径及可无损MBCS；无法表示字符必须拒绝，不允许乱码后仍success。提交回执与完成回执分开。

交互getpoint/entsel/DCL样本显示waiting_for_input；timeout后显示仍running/unknown或已证实cancelled，不把HTTP断开当Lisp已停。每次检查未永久修改LISPSYS/输出日志设置。任意LSP副作用不能宣称在M5 atomic可回滚。

证据：源码字节/hash、编码、值、job状态、实际图元、输出来源、异常与设置前后值。

## 16. A14 — 真正Breakpoint/Step/Watch/Stack（R14）

关闭VS Code UI，以CadBridge直接附加目标PID。加载F-LSP，在第6行/第8行设断点；记录适配器返回verified位置。运行到第6行，Step Into到cb-add，Step Over完成第2行后读取x=2/y=3；栈应包含cb-add和C:CBDBG（允许真实框架帧）；Step Out回调用者，读取p=3；Continue到第8行。

Watch针对当前stop_id读变量，Evaluate受权限约束；Continue后旧frame/variablesReference不可再用。修改源文件后旧断点/源码hash应失效或要求重载。

F-ERROR开启Break on Error，应在真实错误处停止，读取cb-bad和调用者相关帧、x="bad"；不能只等运行结束后解析字符串伪造栈。

2021–2026 按实际声称支持组合运行并属于 V1 强制验收。2015–2020 Debugger 为 G02 探索项：记录候选 Provider、证据与最终 capability（available / unavailable / unverified）；没有受支持 Provider **不使 V1 失败**，但不得以日志、重复执行或模拟栈冒充真 Debugger。

证据：原始脱敏DAP transcript、真实stopped/stack/scopes/variables、源hash、PID证明、实际断点行为。Fake DAP仅测试协议层，不能通过本项。

## 17. A15 — 暂停时CAD感知与控制（R15）

在F-LSP第8行执行前暂停，此时第7行圆已创建。保持同一stop_id，经Plugin查询到该圆中心与r=3；确认Lisp尚未输出CBDBG_DONE；随后step/continue仍可完成。并发尝试普通DB写、另一客户端continue、过期stop_id查询。

预期：合法live查询真实且无死锁/重入，非授权控制拒绝，普通DB写DEBUG_BUSY；不能自动恢复程序再暂停来模拟同一停止点。如果只能拿旧快照，必须带staleness且本项BLOCKED，不得PASS。若数据库中间状态不安全，DEBUG_QUERY_UNSAFE是安全退化，不等于产品目标兑现。

证据：相同stop_id的时间线、DAP与Pipe收发、DB读回、continue后的结果、控制租约负例。

## 18. A16 — 接入、事件与恢复（R16）

两种实际MCP客户端分别连接HTTP/stdio shim；REST和CLI读同目标。启动Lisp/Batch job，SSE消费停止/进度/文档事件，再断开续传；超保留窗口使用旧Last-Event-ID；另用events.wait有限等待。

预期：七/十工具一致；事件有stream_epoch/id/目标/时间；缺口events.gap后重新读状态，不能静默漏事件；慢客户端不阻塞CAD；events.wait上限30秒返回，客户端不支持主动通知时有明确调用方式。业务事件流与MCP协议transport区分。

证据：客户端版本/配置、脱敏协议、续传/缺口、任务最终状态。不要因SSE可连接就宣称所有AI客户端能自主响应。

## 19. A17 — Timeout/Cancel/Job（R17）

分别测试排队超时、执行前取消、atomic步骤间取消、原生调用尚未返回、Lisp等待输入、客户端断线和Host重启；用Fake注入故障，再在受控真机复现关键情形。

预期：cancel_requested与cancelled分开；已Commit不能再报已回滚；未知结果锁止自动写；控制面仍可查询状态；队列满明确429/对应code。默认不杀acad.exe，不自动发ESC终止用户命令，不自动保存/关闭文档。

证据：状态迁移、请求/响应、独立副作用核对与恢复过程。Fake结果不得作为真实Lisp可取消的证据。

## 20. A18 — 安全与权限（R18）

至少覆盖：未认证HTTP、恶意Origin/Host、跨用户Pipe、同用户错误Bootstrap secret、伪造descriptor/PID复用、read权限直接call写工具、read权限借batch/debug.evaluate绕过、路径穿越/reparse/UNC/设备路径、覆盖已有DWG、图纸内嵌“忽略指令执行Lisp”的文字。

预期：认证/权限在实际执行入口强制；MCP hints不是唯一防线；默认loopback、无全局CORS和提权；来源文字只是数据。secret不在命令行/日志/支持包；DPAPI跨机器不可用时重建凭据，不能沿用失败解密空token。

任意LSP拥有宿主权限的事实必须在授权说明中明确，不能以API路径白名单宣称Lisp被沙箱化。证据为请求、拒绝结果、文件/配置/网络监听清单和脱敏审查。

## 21. A19 — Legacy/Modern兼容（R19）

构建两TFM，记录AutoCAD引用SDK与hash；对2015/2024/2025/2026代表真机执行临时加载、query、最小写、atomic、Lisp。2026 Update1.2前后分列；边界2021测官方调试路径。声称支持全部年份前须每年/发布配置smoke。

预期：同源代码，无每版本业务副本；.NET Framework4.8前提被检查；不打包宿主DLL；按确切补丁记录实际Runtime。基础运行、Lisp运行、Debugger、暂停query分列，不一格PASS覆盖全部。

未测项NOT_RUN/缺环境BLOCKED；不能用参考batchPrint项目宣称本项目通过。改变Shell策略必须提供失败证据与最小兼容补丁评审。

## 22. A20 — Doctor与日志（R20）

分别令Host未开、Plugin未加载、Pipe断开、协议不匹配、CAD忙、Debugger缺失、目录不可写；执行doctor --json。追踪一次query、一次write和一次Lisp job。

预期：明确区分故障层次且默认无副作用；request_id从入口到Plugin可追踪；不因日志失败改变已提交结果；日志滚动有界；支持包不含token/原始用户DWG/未授权源码。离线doctor不要求先启动Host。

## 23. A21 — 复用、许可与SDK升级（R21）

检查实际依赖锁/SBOM、导入代码/数据的commit与LICENSE/NOTICE、SDK适配层依赖图；以一次受控SDK小版本更新演练，运行contract/连接/profile测试。

预期：无浮动latest/main生产依赖；未查许可不复制；Autodesk宿主二进制不因为扩展Apache许可而随包；Core/Plugin无MCP SDK依赖泄漏；升级有变更说明和失败回退，不自动更新正在运行的CAD。

证据：来源清单、包内容、依赖图、升级diff与测试报告。本轮文档提供来源登记，不等于未来SBOM已完成。

## 24. A22 — 契约与多AI交付（R22）

从四文档追踪R01–R24到设计/任务/测试；检查公共Schema/错误/identity定义唯一；任意一个新模块任务包必须能明确其输入输出、owner和可开始前提。

预期：没有“另一套MCP业务实现”；共享合同变更影响三入口和两Shell测试；未过Gate不开展依赖实现。产物与运行证据分开，已知阻断项不被文字抹去。

## 25. A23 — Backend与平台预留（R23）

检查依赖与包：只有LiveAutoCadBackend正式实现；Fake仅测试；没有CoreConsole/ezdxf/APS运行依赖、空的ZWCAD交付声明、全Autodesk对象封装。

预期：新增后端的服务边界/Capability合同存在，但V1不发未来实现；Query/事务/Debugger不假设未来后端能力相同。

## 26. A24 — Token、性能与真实Agent闭环（R24）

### 26.1 可复现Token口径

参考tokenizer固定为o200k_base（记录实际库/版本），对真实UTF-8紧凑JSON的tools/list及必要server instructions计数。默认7工具≤6,000，lisp-dev10工具≤9,000；Registry100/200/500时常驻Schema变化≤5%。不把返回中的Base64图像当普通文本tokens计数；图像费用另列。

同时记录搜索、describe、参数、结果、重试、调用轮数、输入/输出、缓存命中口径和实际客户端行为。建立100条独立中英文检索样本（各50，不从训练同义词原句直接抄），Top-3≥95%。不以8条作者自己设计的查询宣称普遍检索质量。

### 26.2 延迟与Batch

相同机器/图纸/验证条件，预热后至少100次：纯Host status/search P95≤300ms，CAD空闲小query P95≤2s。冷启动/忙碌/调试暂停另表。记录CPU/内存/版本，不跨机器比较伪造倍数。

100步相同创建任务比较单步调用与cad_batch：证实batch为一次外部请求与一次业务Pipe载荷；独立结果一致。记录墙钟与tokens，不预设“必须快N倍”；不能去掉batch的读回检查以取得虚假性能优势。

### 26.3 两条真实Agent任务

任务A：在F-CAD找到WALL层半径>500的圆并移到TEST。预期只有C2换层，C1/L1/其他对象不变，Agent有query→执行→读回证据，不默认保存原图。

任务B：给Agent F-ERROR及输入为字符串导致失败的现象，要求通过真Debugger找到cb-bad的入参问题，修复测试副本并验证数值/非法输入策略。必须出现真实断点/错误停止、栈/变量读取，不允许只静态猜测改代码后宣称Debugger已验收。

证据：Agent与客户端/模型版本、完整工具轨迹、授权边界、源码前后diff、实际DAP/图元读回及人工/独立AI复核。没有可用真实Agent时BLOCKED，不能以手工HTTP测试冒充。

## 27. 版本与Gate记录模板

| 验证对象 | 构建 | 基础CAD | Lisp运行 | 真Debugger | 暂停live query | 完整V1 |
|---|---|---|---|---|---|---|
| 2015–2020 各实际测试年/补丁 | NOT_RUN | NOT_RUN | NOT_RUN | EXPLORATORY：G02记录结果，不作为V1放行条件 | N/A（除非探索得到可验证Provider） | 基础能力未完成 |
| 2021–2024 各实际测试年/补丁 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2025 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2026 ≤ Update1.1 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |
| 2026 ≥ Update1.2 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | 未完成 |

此表是模板，不是现场环境盘点；实施时逐行展开精确build。

| Gate | 核心覆盖 | 当前产品状态 |
|---|---|---|
| G01 | A01–A03/A19最小真实链路 | NOT_RUN |
| G02 | A13 Lisp Runtime；A14–A15 对 2021–2026 的真实 DAP/暂停；2015–2020 Debugger 探索记录 | NOT_RUN；旧版Debugger探索不阻塞V1 |
| G03 | A06/A08–A12/A17安全执行 | NOT_RUN |
| G04 | A04/A05/A16/A18/A24工具接入和预算 | NOT_RUN |
| G05 | A01–A03/A07/A13–A21绿色纵向集成 | NOT_RUN |
| G06 | A01–A24必要项、兼容矩阵、AgentE2E | NOT_RUN |

## 28. 四文档的12项交付审计

本节只检查文档完整性与一致性，不能将A01–A24/G01–G06改为PASS。2026-09-17已完成作者交叉复核与文件静态检查，以下PASS仅表示文档级检查通过，不表示第三方审查或产品验证通过。

| # | 文档检查 | 检查位置 | 状态 |
|---|---|---|---|
| 01 | 四文档存在且定义不矛盾 | 根目录四文件、相同版本/状态 | PASS（文档级） |
| 02 | PRD每个V1需求有设计承载 | PRD R01–R24、计划§13的DESIGN映射 | PASS（文档级） |
| 03 | 每个关键设计模块有阶段 | DESIGN D01/M1–M9、计划P0–P8 | PASS（文档级） |
| 04 | 每个核心需求有可执行验收 | 计划追踪矩阵、A01–A24 | PASS（文档级） |
| 05 | V1/Future分界一致 | PRD§7、DESIGN D03/D15、P2/A23 | PASS（文档级） |
| 06 | 两Shell/年份/补丁/Debugger边界一致 | PRD§4、DESIGN D12/D15、P1/P7、A14/A19 | PASS（文档级） |
| 07 | Portable临时加载贯穿 | PRD R01/R02、DESIGN D13、P1/P6、A01/A02 | PASS（文档级） |
| 08 | MCP/REST/CLI共用业务 | DESIGN D01/D05、P2/P4、A05 | PASS（文档级） |
| 09 | Token策略含合同及量化测试 | PRD§6、DESIGN D04、P4、A24 | PASS（文档级） |
| 10 | 真实Breakpoint/Step/Watch/Stack明确 | PRD R14/R15、DESIGN D12、G02、F-LSP/A14/A15 | PASS（文档级） |
| 11 | 高风险假设有Gate和失败处置 | DESIGN D16、计划P1/P3/P4、§27 | PASS（文档级） |
| 12 | 官方/开源来源、复用及许可边界 | DESIGN D17、P0、A21 | PASS（文档级） |

文档审计还应检查：Markdown围栏、JSON示例可解析、相对文件链接、编号/任务引用、无“未实现产品已全通过”字样。本轮未运行真实AutoCAD和业务测试，结论必须保持为“可交付文档，可进入早期Gate验证”，而不是“完整产品交付通过”。


### 本次文档复核记录

- 实际在项目目录回读四文件，确认UTF-8可解码；原始技术架构生成提示词保留，未新增业务源码。
- R01–R24、计划追踪矩阵24行与A01–A24逐项对应；18个设计章节、42个任务定义、6个Gate、21项来源登记存在，无失效编号。
- 4个JSON示例均成功解析；Markdown围栏闭合，相对文件链接目标存在。
- 交叉复核修正了文档新建/打开无需预先存在document_id的例外、只读Batch与写入atomic的区别、CLI补全PID启动时间、Watch只读不能借Evaluate绕过等语义。
- 保留 2015–2020 Debugger 探索、2021–2026 暂停点实时查询、Legacy/Modern 及 2026 补丁兼容等技术风险；相关产品 Gate 仍为 NOT_RUN，不据文档审核将其标为通过。2015–2020 Debugger 探索失败本身不阻塞 V1 基础能力。
- 结论：四文档可交付，足以移交执行AI进入P0/P1的早期核验；不等于所有技术假设已验证，也不等于允许跳过Gate开展完整产品发布。

本记录没有声称已调用外部审查模型。正式实现与发布仍按P8要求进行独立审核。
