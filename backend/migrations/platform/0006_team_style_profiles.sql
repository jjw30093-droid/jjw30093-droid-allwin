-- Studio 球队打法画像(runtime/platform only).
--
-- 该表保存由已经落盘的 FotMob team artifact 计算出的版本化画像。生产公开 API
-- 不读取本表；它只服务 analyst/admin Studio 与 daily-content replay。
--
-- 自然键固定为(match_id, data_cutoff_at)。同一截止时刻只能对应一份 source_hash /
-- payload；完全相同 replay 由 service 跳过，内容变化必须显式冲突，不能覆盖历史。

CREATE TABLE team_style_profiles (
  id              TEXT PRIMARY KEY,
  match_id        INTEGER NOT NULL,
  league_id       INTEGER NOT NULL,
  season          TEXT NOT NULL,
  data_cutoff_at  TEXT NOT NULL,
  source_hash     TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  UNIQUE (match_id, data_cutoff_at)
);

CREATE INDEX idx_team_style_profiles_match_cutoff
ON team_style_profiles(match_id, data_cutoff_at DESC);

CREATE TRIGGER trg_team_style_profiles_no_update
BEFORE UPDATE ON team_style_profiles
BEGIN
  SELECT RAISE(ABORT, 'team style profile is append-only');
END;

CREATE TRIGGER trg_team_style_profiles_no_delete
BEFORE DELETE ON team_style_profiles
BEGIN
  SELECT RAISE(ABORT, 'team style profile is append-only');
END;

