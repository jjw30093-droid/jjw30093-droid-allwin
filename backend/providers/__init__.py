"""Provider Adapter 层:外部数据源(NowGoal / FotMob / 公开实时比分)的解析与抓取。

纯解析函数与网络获取分离:解析函数不碰网络,可离线单测;
抓取函数失败抛异常,由调用方写 source_health(backend/ingest/odds_snapshots.py)。
"""
