# core(data/allwin.db)migrations

现有 allwin.db 的 18 张表由历史 `backend/init_db.py` 建立,早于 migration 体系。
本目录用于**今后**对 allwin.db 的 schema 变更(必须先备份,见 CLAUDE.md §5.2)。
migration runner 首次运行会在 allwin.db 内创建 `schema_migrations` 表(非破坏性)。
现有表不得破坏性重建。
