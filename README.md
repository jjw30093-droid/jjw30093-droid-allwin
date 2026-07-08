# allwin (欧赢)

独立项目,与 miaomiaodi.cc / miaomiaodi.vip 完全分开:独立目录、独立 git 仓库。

当前阶段:纯本地开发。
- `backend/`:FotMob 爬虫 + SQLite 落库脚本
- `data/`:本地 SQLite 数据库(不进 git)

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 手填 THORDATA_PROXY
python backend/init_db.py
python backend/ingest/ingest_match.py <match_id>
```

详见 [CLAUDE.md](CLAUDE.md)。
