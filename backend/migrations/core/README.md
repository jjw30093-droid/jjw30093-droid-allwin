# core(data/allwin.db)migrations

现有 allwin.db 的 18 张表由历史 `backend/init_db.py` 建立,早于 migration 体系。
本目录用于**今后**对 allwin.db 的 schema 变更(必须先备份,见 CLAUDE.md §5.2)。
migration runner 首次运行会在 allwin.db 内创建 `schema_migrations` 表(非破坏性)。
现有表不得破坏性重建。

当前迁移:

- `0001_dim_match_kickoff.sql`:为 legacy `dim_match` 增加精确 kickoff;
- `0002_kickoff_provenance.sql`:增加 kickoff precision/source;
- `0003_schedule_state_v1.sql`:新增稳定 identity、append-only state/
  observation、deterministic current projection 与 versioned rest lineage。

`0003` 与 legacy `dim_match` 并存，不迁移或回填真实行，不改变既有 reader/writer
语义。本轮只在新建 `/tmp` SQLite 完成 migration/rollback/state proof；真实
`data/allwin.db` 尚未应用。正式 ingestion、Worker、API 和 frontend 仍未开始。
