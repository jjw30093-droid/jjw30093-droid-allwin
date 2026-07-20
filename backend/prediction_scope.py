"""正式预测样本永久资格口径的唯一真源(CLAUDE.md §9.1)。

track record、evaluation、daily manifest 三处必须共用同一判据,避免口径漂移
(历史上 manifest 只查 is_official 导致与 track record/evaluation 不一致,已收口)。

永久资格 = 官方 ∧ 曾锁定 ∧ 已发布 ∧ 有精确开球 ∧ 发布严格早于开球:

    is_official = 1
    AND locked_at IS NOT NULL
    AND published_at IS NOT NULL
    AND kickoff_at_utc IS NOT NULL
    AND julianday(published_at) < julianday(kickoff_at_utc)

用 julianday() 而非裸文本比较:文本比较对混合时区偏移格式(如 kickoff='...-05:00'
vs published='...Z')会给出错误结论。

绝不使用 status / retracted / superseded_by 排除已经取得资格的样本——撤回与被取代
只是透明标注,不改变公开列表、manifest 或评估分母(§9.1 永久资格不变量)。
"""


def official_sample_where(alias: str = "s") -> str:
    """返回正式样本永久资格的 SQL WHERE 片段。

    alias:prediction_snapshots 的表别名(如 's');传空串则不加前缀,
    适用于无别名的单表查询。片段内所有列都带同一前缀,可直接拼进 WHERE。
    """
    p = f"{alias}." if alias else ""
    return (
        f"{p}is_official = 1\n"
        f"        AND {p}locked_at IS NOT NULL\n"
        f"        AND {p}published_at IS NOT NULL\n"
        f"        AND {p}kickoff_at_utc IS NOT NULL\n"
        f"        AND julianday({p}published_at) < julianday({p}kickoff_at_utc)"
    )
