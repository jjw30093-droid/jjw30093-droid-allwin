"""/api/v1/simulator/*:模拟器参数下发(2026-10-02 登录门禁)。

未登录只能用英超模拟器(英超页由 Next 服务端直接读参数文件渲染);其他联赛
的参数与跨联赛的真实比赛索引(含盘口线)必须经这里校验登录后下发。
切片逻辑见 backend/queries/simulator_params.py。响应随身份变化 → no-store。
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Response

from backend.queries import simulator_params as q_sim
from backend.queries.leagues import ANON_LEAGUE_IDS

from .data_access import data_access_ctx, require_league_access
from .deps import NO_STORE, AuthContext
from .schemas import SimulatorFixturesResponse, SimulatorParamsResponse, error_responses

router = APIRouter(
    prefix="/api/v1/simulator",
    tags=["simulator"],
    responses=error_responses(401, 404, 422, 429, 503),
)


def _load_or_raise() -> dict:
    # 与页面同一个开关:未上线/暂停时接口同样不可用
    if os.environ.get("SIMULATOR_ENABLED") != "1":
        raise HTTPException(status_code=404, detail="模拟器未开放")
    params = q_sim.load_params()
    if params is None:
        raise HTTPException(status_code=503, detail="模拟器参数暂不可用")
    return params


@router.get("/params", response_model=SimulatorParamsResponse)
def simulator_params(
    league_id: int,
    response: Response,
    ctx: AuthContext = Depends(data_access_ctx),
):
    response.headers["Cache-Control"] = NO_STORE
    params = _load_or_raise()
    if league_id not in q_sim.calibrated_available(params):
        raise HTTPException(status_code=404, detail="该联赛暂无模拟器参数")
    require_league_access(ctx, league_id)
    return {
        "league_id": league_id,
        "params": q_sim.slice_for_league(params, league_id),
        "fixture_index": q_sim.fixture_index(
            params, None if ctx.authenticated else set(ANON_LEAGUE_IDS)
        ),
    }


@router.get("/fixtures", response_model=SimulatorFixturesResponse)
def simulator_fixtures(
    response: Response,
    ctx: AuthContext = Depends(data_access_ctx),
):
    """真实比赛索引:登录给五大联赛全部,未登录只给英超。"""
    response.headers["Cache-Control"] = NO_STORE
    params = _load_or_raise()
    return {
        "fixture_index": q_sim.fixture_index(
            params, None if ctx.authenticated else set(ANON_LEAGUE_IDS)
        ),
    }
