/**
 * MarketCard:四种 data_quality/signal_grade 组合必须渲染不同文案,
 * 尤其是 data_quality='ok' 但 signal_grade=null 这个最容易被漏判的状态
 * (预估值和历史命中率都算得出来,只是没通过外样本单调性检验——绝不能
 * 因为"有数字"就展示成有信号)。
 */

import { cleanup, render, screen, within } from "@testing-library/react";
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
  // Fix 1(2026-08-19):该市场全部已标定线各自的回测结果——大多数既有测试
  // 不关心这个字段,留空数组即可(折叠区"各线回测"小节据此不渲染)。
  lines: [],
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
      expect(screen.getByText(/历史命中率不够稳/)).not.toBeNull();
      // hit_rate 的具体数值不应该被当结论展示出来
      expect(screen.queryByText(/51%/)).toBeNull();
      // 2026-08-21:estimate 非空时,空白态不再放"暂无倾向"这种空话,改成
      // 事实对比——两队合计与对比盘口线的差值(BASE.estimate=5.2,line=3.5)。
      expect(screen.queryByText("暂无倾向")).toBeNull();
      // "5.2" 在结论格与折叠区"两队近 10 场自身历史均值合计"里各出现一次,
      // 两处都是真实内容,不是重复渲染。
      expect(screen.getAllByText("5.2").length).toBeGreaterThan(0);
      expect(screen.getByText("两队近 10 场合计")).not.toBeNull();
      expect(screen.getByText(/\+1\.7/)).not.toBeNull();
      expect(screen.getByText("对比盘口线")).not.toBeNull();
    },
  );

  it("data_quality='ok' 但 signal_grade=null 且 estimate 也是 null 时,回退到旧的「暂无倾向」空态", () => {
    // 理论上 data_quality='ok' 蕴含 estimate 非空(后端 market_cards.py 只在
    // h_avg/a_avg 都非空时才判 ok),这里防御性覆盖一次契约意外被打破的情况。
    const card: MarketCardData = { ...BASE, signal_grade: null, lean: null, estimate: null };
    render(<MarketCard card={card} />);
    expect(screen.getByText("暂无倾向")).not.toBeNull();
    expect(screen.queryByText("两队近10场合计")).toBeNull();
  });

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
    expect(screen.getByText(/场次还不够多/)).not.toBeNull();
    expect(screen.queryByText(/两队近 10 场自身历史均值合计/)).toBeNull();
  });

  it("data_quality='no_history' 时提示联赛数据补采", () => {
    const card: MarketCardData = { ...BASE, data_quality: "no_history", estimate: null, signal_grade: null, lean: null };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/历史数据还没补齐/)).not.toBeNull();
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

describe("MarketCard 折叠区:各线回测 + 对手侧 + 回测更新时间(数据倾向卡片填充 2026-08-19)", () => {
  it("未定级的线在折叠区显示'未定级'且不出现任何百分比", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      lines: [
        { line: 8.5, is_default: false, signal_grade: "★★", hit_rate: 0.62, sample_size: 210 },
        { line: 9.5, is_default: true, signal_grade: null, hit_rate: null, sample_size: null },
      ],
    };
    render(<MarketCard card={card} />);
    const row = screen.getByText(/未定级/).closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).queryByText(/%/)).toBeNull();
  });

  it("已定级的其它线展示真实★与命中率", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      signal_grade: null,
      lean: null,
      hit_rate: null,
      lines: [
        { line: 8.5, is_default: false, signal_grade: "★★", hit_rate: 0.62, sample_size: 210 },
        { line: 9.5, is_default: true, signal_grade: null, hit_rate: null, sample_size: null },
      ],
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/★★/)).not.toBeNull();
    expect(screen.getByText("62%")).not.toBeNull();
  });

  it("对手侧表格渲染 against 数值,且每格各自标注自己的实际场次", () => {
    const card: MarketCardData = {
      ...BASE,
      driver_factors: [
        { key: "yellow_cards", for: { avg: 2.6, n: 10 }, against: { avg: 1.9, n: 8 } },
        { key: "touches_opp_box", for: { avg: 8.1, n: 10 }, against: { avg: 7.4, n: 10 } },
      ],
      driver_factors_away: [
        { key: "yellow_cards", for: { avg: 3.1, n: 10 }, against: { avg: 2.2, n: 9 } },
        // touches_opp_box 全库仅 89% 覆盖(与 100% 覆盖的指标口径不同),
        // 用一个不同于其它行的 n 值证明场次是逐指标标注,不是共用一个数。
        { key: "touches_opp_box", for: { avg: 7.9, n: 10 }, against: { avg: 6.8, n: 89 } },
      ],
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/对手打成什么样/)).not.toBeNull();
    expect(screen.getByText("1.9")).not.toBeNull();
    expect(screen.getByText("2.2")).not.toBeNull();
    // 两处场次数字都必须各自出现,不能被压成同一个"共 N 场"
    expect(screen.getByText(/8场/)).not.toBeNull();
    expect(screen.getByText(/89场/)).not.toBeNull();
  });

  it("驱动因子标签不出现英文 key(§11.2 内部枚举值泄漏)", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      driver_factors: [driver("blocked_shots", 4.2)],
      driver_factors_away: [driver("blocked_shots", 3.8)],
    };
    render(<MarketCard card={card} />);
    expect(screen.queryByText("blocked_shots")).toBeNull();
    // "射门被封堵"合法出现两次(为/对手侧各一张表的同一行标签),用 getAllByText
    // 而不是 getByText——两处都是真实内容,不是重复渲染。2026-08-23 采纳
    // FotMob 官方措辞,此前叫"封堵射门"。
    expect(screen.getAllByText("射门被封堵").length).toBeGreaterThan(0);
  });

  it("展示回测数据更新时间(北京时间)", () => {
    const card: MarketCardData = { ...BASE, calibrated_at: "2026-08-01T03:00:00Z" };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/回测数据更新于/)).not.toBeNull();
    expect(screen.getByText(/北京时间/)).not.toBeNull();
  });

  it("线不在已标定集合时,文案说明是哪条真实线没回测过,而不是笼统文案", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      line: 10,
      data_quality: "no_calibration",
      no_calibration_reason: "line_not_calibrated",
      signal_grade: null,
      lean: null,
      hit_rate: null,
      sample_size: null,
      bucket_index: null,
      lines: [
        { line: 8.5, is_default: false, signal_grade: "★★", hit_rate: 0.62, sample_size: 210 },
        { line: 9.5, is_default: false, signal_grade: null, hit_rate: null, sample_size: null },
        { line: 10.5, is_default: false, signal_grade: "★", hit_rate: 0.55, sample_size: 180 },
      ],
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/这条没验过，所以不给倾向/)).not.toBeNull();
    expect(screen.getByText(/真实盘口是 10/)).not.toBeNull();
    expect(screen.getByText(/8\.5.*\/.*9\.5.*\/.*10\.5/)).not.toBeNull();
    expect(
      screen.queryByText("该盘口线暂无历史回测数据,仅展示两队近期数据对比。"),
    ).toBeNull();
  });

  it("calibration_line 与 line 不同(整数线映射到半线)时,折叠区渲染等价说明", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      line: 10,
      calibration_line: 10.5,
      lines: [{ line: 10.5, is_default: true, signal_grade: "★", hit_rate: 0.55, sample_size: 180 }],
    };
    render(<MarketCard card={card} />);
    expect(screen.getByText(/10 和 10\.5 其实是同一条线/)).not.toBeNull();
    expect(screen.getByText(/走水/)).not.toBeNull();
  });

  it("calibration_line 等于 line(未发生映射)时,不渲染等价说明", () => {
    const card: MarketCardData = {
      ...BASE,
      market: "corners",
      line: 10.5,
      calibration_line: 10.5,
      lines: [{ line: 10.5, is_default: true, signal_grade: "★", hit_rate: 0.55, sample_size: 180 }],
    };
    render(<MarketCard card={card} />);
    expect(screen.queryByText(/同一件事/)).toBeNull();
    expect(screen.queryByText(/走水/)).toBeNull();
  });
});
