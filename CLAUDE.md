# allwin (欧赢) — 项目约定

- 与 `miaomiaodi.cc` / `miaomiaodi.vip` 完全独立:独立目录、独立 git 仓库,不共享代码/数据。
- 当前阶段:纯本地开发,尚未部署到服务器。
- 数据库:本地 SQLite,固定路径 `data/allwin.db`(见 `backend/db.py` 的 `DB_PATH`)。以后切服务器部署时只改这一处。
- 凭证:thordata 代理凭证只存在 `.env`(不进 git),`backend/fotmob_client.py` 通过 `os.environ["THORDATA_PROXY"]` + `load_dotenv()` 读取。真实凭证由用户手填,不写进代码/命令。
- 联赛范围:目前只做英超,`league_id=47`。
- 表结构定义在 `backend/schema.py`,列名严格对齐 `fotmob_client.py` 里 `parse_*` 方法返回字典的 key:
  - `dim_match`(parse_match_dim,主键 Match_ID)
  - `dim_player`(player_id → name)
  - `fact_shotmap`(parse_shotmap_records,无唯一键)
  - `fact_player_match_stats`(parse_player_stats_records,固定宽表)
  - `fact_team_match_stats`(parse_team_stats_records,核心列 + `extra_json` 兜底动态字段——原因见 `schema.py` 注释)
- 落库幂等策略(`backend/ingest_match.py`):按 match_id 粒度,dim 表 upsert(INSERT OR REPLACE),fact 表先 `DELETE WHERE Match_ID=?` 再插入,不依赖行级唯一键。
- 验证步骤只贴真实终端 stdout,不写"已完成/已同步"类摘要。
- 建 GitHub 远程仓库 / push 前必须先问用户确认 repo 名和 owner。
