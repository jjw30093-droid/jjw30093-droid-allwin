-- 0020: fact_player_match_stats 加两列(2026-09-23)。表本身已由 0007 建过
-- 骨架,这里不用再重复 CREATE TABLE IF NOT EXISTS,直接加列。
--
-- 背景:围绕"能不能做球员 Pizza/雷达图"的数据审计(先 APK 反编译摸底,
-- 再用生产 FotMobClient 对真实比赛发起请求核对,两步都做完才落这个
-- migration,不是只凭 APK 字段名就动手)。
--
-- 1) aerials_won_total:争顶成功率的分母。原始 payload 的 aerials_won
--    本来就是 fractionWithPercentage({"value":2,"total":4}),跟
--    accurate_passes_total 是同一个既有先例(_build_stat_lookup 早就在
--    产出 "{key}__total" 这个派生 key),这里只是把一直在流经解析器、
--    此前没有列去接住的字段真正落库,不需要新增外部请求。
--
-- 2) line_breaking_passes:FotMob 对"推进/破线传球"的真实字段。2026-09-23
--    用生产 FotMobClient 对英超(5795455)和意甲(5749680)各一场真实完赛
--    比赛发起请求验证:英超那场全部上场球员都有这个 stat 的非空整数值
--    (如 Mac Allister 19、António Silva 25),意甲那场全场 50 人的 stat
--    标题里完全没有这一项。覆盖率按联赛的 Opta 深度分级(欧冠/英超最全,
--    其余联赛目前是合法 NULL,不是解析错误),不强求全量覆盖再上线。
--
-- 同一次核对还确认 APK 反编译摸到的 KeyPasses/AccurateForwardZonePass/
-- AccurateBackZonePass 三个字段,在这两场真实比赛里都没有出现在返回结果
-- 里(是 App 数据类的旧字段,不是当前接口真的会给),所以本次不加这三列。

ALTER TABLE fact_player_match_stats ADD COLUMN "aerials_won_total" REAL;
ALTER TABLE fact_player_match_stats ADD COLUMN "line_breaking_passes" REAL;
