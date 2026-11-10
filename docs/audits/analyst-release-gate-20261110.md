# 分析报告上线前口径审查 · 2026-11-10

本轮检查CC Opta→报告投影→解释→PDF的关键口径。不是对所有供应商字段或全部历史比赛的全面认证。

已证实并修复：

1. 转换判断不再混用整场pressing与11v11时段，改用经射门身份核验、去重的选区“夺回球权后15秒射门”；不是全部球权丢失后的威胁。
2. 进球/助攻/关键传球/抢断/拦截等官方整场榜与射门/推进的选区榜分别标明，禁止用整场进球除选区射门。
3. 5/10场背景沿用相同筛选，5场是10场子集；修正此前“完整常规时间”的错误说明。
4. 传接网络明确整场边、选区动作均位节点的不同范围，不当作同一区间无球阵型。
5. losses区域改为被断球及失误（事件50/51），不宣称是全部球权丢失。
6. 联赛区域xG不完整时置未知，均值仅纳入各格完整覆盖球队并显示覆盖；缺事件、阶段或射门上下文不填假零。

真实独立SQL：桑德兰5场372.483333分钟、53射、5.984138175 xG；切尔西5场444.483333分钟、57射、5.228469663 xG；事件身份无重复。分钟由原始阶段边界与比分/人数区间独立求交验证，未复用显示helper。

选区夺回后15秒：桑德兰本队2射/0.195104655，对手7射/1.247773938；切尔西本队7射/0.518177470，对手3射/0.337266473。逐动作xT1184/1641条，合计2.688212091/1.721995970，与球员汇总相同。有值xA总/分项对账；xA不按选区拆分。

历史样本明确重建，当前模型评分不证明历史截止前已存在模型；未认证历史模型训练无泄漏。无球位置与物理门框坐标不推造。未把回放当真实当时预测。

生产运行门禁：原预测服务CPU20%/192MB限制下读取中断，不放行；独立分析候选CPU100%/512MB同场9.47秒、峰值94780KiB、JSON7467007字节。原预测服务保持隔离。需新增受限8769隧道、平台0024迁移、PDF依赖及TrueType中文字体。用户已授权任一现有管理员作为自动报告owner。

审查证据：/tmp/cc-final-independent-sql.py及其result.json、/tmp/cc-release-reviewed-packet.json、/tmp/cc-final-resource-profile-isolated.json、/tmp/report-release-gate/*manifest.json。修正PDF主文43页/含附录153页；受影响转换与基准页渲染核对。

生产首轮发布 e9c0becec296 已切换，迁移0024、三库完整备份与健康/业务冒烟通过。CC独立只读分析服务2479244已运行；原预测服务未切换。自动工作流20个实际任务成功，无失败。

线上按真实业务方法生成并冻结桑德兰—切尔西赛前/赛后、森林—曼城赛后报告；射门53/57、9/10、11/12与审查样本一致，赛后分别xG2.381788537/0.657988288与1.055496333/1.758843705。PDF/JSON均成功，赛前PDF152页、赛后122页，无空页或替代字符，首/6/15页已渲染核对。报告均待人工审核；历史赛前明确重建。

浏览器登录管理员实际打开CC报告后发现两项交付问题：shared .env的ALLOWED_ORIGINS仅含VIP，覆盖了原systemd四域白名单；已保留既有配置备份并恢复既有CC/VIP四域，管理员鉴权与CSRF不变。之后真实浏览器POST导出200。生成后window.open被Chrome拦截；改用现有downloadServerExport带cookie下载Blob，下载失败显式提示，补PDF/JSON及下载错误回归。

Cloudflare两站10个相关HTML入口已定向purge成功，不删除旧hash资源。后续下载修正版本需重新构建、发布、浏览器实际下载验收。


下载修正验证：Vitest定向3项通过，ESLint通过，带next-env.d.ts的定向TypeScript检查通过。额外执行的全仓npx tsc --noEmit暴露已有测试fixture与当前API类型不一致（AccessTab/RecoTab导出、标准身份与旧数字ID等），未修改这些无关测试或降低检查规则；Next生产类型检查沿用框架标准诊断范围。本次变更没有业务类型错误。原本单独定向配置未加载next-env导致CSS模块类型不可见，补入既有声明后通过；未改项目tsconfig。

第一轮release所有发布门禁通过；非发布门禁的旧备份压缩停留在20261106备份的只读完整性验证，为避免占用后续修正版发布I/O，核对三库原文件及metadata仍完整后停止该压缩任务，原备份与最新发布备份均保留。没有终止或跳过发布前备份及其校验。


下载修正版npm ci期间，2GB主机内存/磁盘压力使部分API及文件请求超时，另有既有标准预览查询触发SQL deadline；未将这些504误判为报告数字问题。恢复API后healthz=200/0.004821s；继续降低候选构建线程优先级，并须在最终版本检查报告导出完整GET200。该阶段不是已验证下载成功声明。


## Final production acceptance

Runtime: website ceeda0b3b9fa; isolated CC analyst API2479244. The second production build, TypeScript pass,24 generated pages, browser/public-URL/static-asset gates, strict three-database backup20261110T042212Z, migration status, candidate/live healthz and readyz, business smoke and public-site checks passed. Targeted Cloudflare HTML purges succeeded for both domains.

Chrome, using the existing real administrator session, opened the Sunderland-Chelsea report with release=ceeda0b3b9fa. Clicking Export PDF produced cc-report-prematch-6c22ce98-6712-4bc0-b828-5efe86608030-v1.pdf,1,053KB, with the browser download status Complete. This verifies actual authenticated HTTP generation and download, rather than dependency overrides or a fabricated login. The earlier incognito check showed only the administrator-login requirement; the anonymous source endpoint returned401/private,no-store.

Automatic jobs:20 succeeded,0 failed. Saved draft IDs: prematch6c22ce98-6712-4bc0-b828-5efe86608030; linked postmatch d61400b8-1a6d-4b18-92d0-da87b0107032; second-match postmatch f6e2043b-fdd2-4a41-a882-dd3412c7c282. Official xA coverage24/25 and22/26 players;73/74 player-match xA component records. Saved per-action xT counts1184/1641 and sums2.6882120909746905/1.7219959696748512 reconcile.

The optional old-backup compression was stopped during read-only verification after confirming all three original backup files and metadata remained intact. Both mandatory release backup sets remain complete; no pre-migration integrity check was skipped. Nine local database/sidecar sizes and mtimes match the protected baseline. No full database was downloaded, no model was trained, and the original CC prediction runtime was not switched.

A future fixture's actual predicted-XI to confirmed-XI to final-whistle chronology still requires a future real match; it has not been represented as completed. Historical prematch reports remain explicitly reconstructed and pending human review. The repository-wide legacy fixture type errors are documented above; only the scoped check and production TypeScript build are claimed to pass.
