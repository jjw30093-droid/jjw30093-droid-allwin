/**
 * MarketCard:四种 data_quality/signal_grade 组合必须渲染不同文案,
 * 尤其是 data_quality='ok' 但 signal_grade=null 这个最容易被漏判的状态
 * (预估值和历史命中率都算得出来,只是没通过外样本单调性检验——绝不能
 * 因为"有数字"就展示成有信号)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarketCard } from "@/components/matches/MarketCard";
import type { MarketCard as MarketCardData } from "@/lib/api-v1";

afterEach(cleanup);

function driver(key: string, forAvg: number | null, n = 10): MarketCardData["driver_factors"][number] {
  return { key, for: { avg: forAvg, n }, against: { avg: forAvg, n } };
}

const BASE: MarketCardData = {
  market: "yellow_cards",
  label: "罚牌",
  line: 3.5,
  line_source: "statistical",
  estimate: 5.2,
  bucket_index: 3,
  hit_rate: 0.58,
  sample_size: 400,
  signal_grade: "★★",
  lean: "over",
  calibration_scope: "all_leagues",
  data_quality: "ok",
  driver_factors: [driver("yellow_cards", 2.6), driver("fouls", 13.4)],
  driver_factors_away: [driver("yellow_cards", 3.1), driver("fouls", 14.0)],
};

describe("MarketCard 结论区", () => {
  it("有信号时渲染倾向 + 星级 + 历史命中率(2026-08-14 重设计:倾向/命中率两格,不是一句话)", () => {
    render(<MarketCard card={BASE} />);
    expect(screen.getByText("罚牌")).not.toBeNull();
    // 结论区是"偏大"(倾向格大字)+"数据倾向"(格下小字说明)两个独立元素,
    // 不再是"数据倾向:偏大"这样一句话——用这两个断言精确区分两处,
    // 避免和折叠区"倾向来自离线历史回测"这句话互相打架。
    expect(screen.getByText("偏大")).not.toBeNull();
    expect(screen.getByText("数据倾向")).not.toBeNull();
    expect(screen.getByText("★★")).not.toBeNull();
    // "58%" 同时出现在结论格大字和下方说明句里,两处都是真实内容,不是重复渲染
    expect(screen.getAllByText(/58%/).length).toBeGreaterThan(0);
    expect(screen.getByText("历史命中率")).not.toBeNull();
  });

  it(
    "data_quality='ok' 但 signal_grade=null 时(外样本不单调)绝不渲染倾向," +
      "即使 hit_rate 和 estimate 都是真实数字",
    () => {
      const card: MarketCardData = {
        ...BASE,
        market: "corners",
        signal_grade: null,
        lean: null,
        hit_rate: 0.5148, // 真实存在的数字,但不该被展示成"信号"
      };
      render(<MarketCard card={card} />);
      expect(screen.queryByText("偏大")).toBeNull();
      expect(screen.queryByText("偏小")).toBeNull();
      expect(screen.getByText("暂无倾向")).not.toBeNull();
      expect(screen.getByText(/样本外测试中不够稳定/)).not.toBeNull();
      // hit_rate 的具体数值不应该被当结论展示出来
      expect(screen.queryByText(/51%/)).toBeNull();
    },
  );

  it("data_quality='no_calibration' 时展示'暂无历史回测数据'", () => {
    const card: MarketCardData = {
      ...BASE,
      data_quality: "no_calibration",
      signal_grade: null,
      lean: null,
      hit_rate: null,
      sample_size: null,
      bucket_index: null,
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/暂无历史回测数据/)).not.toBeNull();
  });

  it("data_quality='insufficient_sample' 时不展示预估值", () => {
    const card: MarketCardData = {
      ...BASE,
      data_quality: "insufficient_sample",
      estimate: null,
      signal_grade: null,
      lean: null,
      hit_rate: null,
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/历史场次不足/)).not.toBeNull();
    expect(screen.queryByText(/两队近 10 场自身历史均值合计/)).toBeNull();
  });

  it("data_quality='no_history' 时提示联赛数据补采", () => {
    const card: MarketCardData = { ...BASE, data_quality: "no_history", estimate: null, signal_grade: null, lean: null };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/该联赛历史数据补采中/)).not.toBeNull();
  });

  it("line_source='market' 时标「真实盘口」,不能和统计参考线看起来一样", () => {
    const card: MarketCardData = { ...BASE, market: "corners", line: 10.5, line_source: "market" };
    render(<MarketCard card={card} />);
    expect(screen.getByText("真实盘口")).not.toBeNull();
    expect(screen.queryByText("历史参考线")).toBeNull();
  });

  it("line_source='statistical' 时标「历史参考线」(默认态,BASE 本身就是)", () => {
    render(<MarketCard card={BASE} />);
    expect(screen.getByText("历史参考线")).not.toBeNull();
    expect(screen.queryByText("真实盘口")).toBeNull();
  });

  it("样本不足的驱动因子显示占位符,不是 0", () => {
    const card: MarketCardData = {
      ...BASE,
      driver_factors: [driver("touches_opp_box", null, 2)],
      driver_factors_away: [driver("touches_opp_box", null, 2)],
    };
    render(<MarketCard card={card} />);
    // avg=null 必须渲染成占位符,不能被 toFixed 之类的调用变成 "0.0"
    expect(screen.queryByText("0.0")).toBeNull();
  });
});
