# ROADMAP — 欧赢 / allwin

> **本文件已退休为指针。** 旧版按 Phase 1-4 排的数据层进度表已被验证为与实际不符（Silver/Gold/Serving/Frontend/Auth 早已上线，五大联赛已在 Phase 3 完成，Phase 4 点名的挪超反而是当前唯一有真实周内实测数据的联赛）——继续维护这份表格只会产生新的错误信息，故整体移除，改为指向真实状态与规划的入口。

- **数据层当前覆盖 + 依赖排序的前向计划** → `docs/data-plan.md`
- **某一轮做了什么、真实命令输出** → `docs/current-state.md`
- **数据源能力与验证状态** → `docs/data-sources.md`
- **逐模块独立复核记录** → `PLANS.md` + `docs/audits/`
- **架构与运行拓扑（不变的部分）** → `docs/architecture.md`

源码中仍有少量注释引用旧版 "Phase 2"/"Phase 3" 编号（例如 `backend/schema.py`、`backend/api_server.py`、`DESIGN.md` 的赔率 tab 相关 token 注释）——这些编号不再对应本文件任何章节，含义按上下文语境理解（"Phase 2" 历史上大致指"赔率与付费深度内容"），后续修改这些代码时可顺手去掉编号引用，但不必为此专门开一轮清理任务。
