/**
 * ProjectedLineupSection 诚实文案测试(PIPELINE_REDESIGN_V2 P2 + 2026-08-18
 * 阵容采集窗口 72h + 主教练 + 替补诚实空态;2026-08-25 纵向双队化重写)。
 *
 * 历史两个真实缺陷(P2,保留):
 * 1. 组件原来无条件承诺"更新后本区会换成「已确认首发」"——但真实抓取
 *    228 行 bronze_fm_lineup_snap 里 lineup_type 从未出现过 "confirmed",
 *    这是一个产品永远兑现不了的承诺(CLAUDE.md §2.2 禁止编造能力)。
 * 2. lineup_type="predicted"(source="enetpulse",16 行真实数据)被无条件
 *    渲染成"数据源给的是两队上一场的首发"——但 Enetpulse 的 predicted 是
 *    第三方对本场比赛的预测阵容,不是上一场的真实首发。
 *
 * 2026-08-25 结构变化(站长验收返工,对齐 FotMob 恒纵向布局):
 * - 主/客 tab 移除,两队同屏——教练/替补随之改两列并排,相关断言从
 *   "切换后互斥"改为"同时可见";
 * - 球场从"半场 + 按阵型分行"换成共享的 VerticalPitchFormation(纵向整场
 *   viewBox 0 0 68 105,预计首发用石板灰 probable 变体);rowsFor 已删除,
 *   坐标映射的纯函数断言在 vertical-pitch-formation.test.tsx;
 * - 旧快照无坐标时的降级从"球场位置的纯名单"变为"各队列内的纯名单"。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ProjectedLineupSection } from "@/components/matches/ProjectedLineupSection";
import type { components } from "@/lib/api-types";

afterEach(cleanup);

type LineupSide = components["schemas"]["MatchPreviewLineupSideDTO"];

function side(over: Partial<LineupSide> = {}): LineupSide {
  return {
    team_id: 1,
    formation: "4-3-3",
    starters: [{ id: 1, name: "球员一", shirt_number: "1" }],
    subs: [],
    ...over,
  };
}

const BASE_PROPS = {
  homeName: "主队",
  awayName: "客队",
  source: null as string | null,
  observedAt: "2026-08-15T06:03:11Z",
  home: side(),
  away: side({ team_id: 2 }),
  homeSidelined: [],
  awaySidelined: [],
};

const UNKEEPABLE_PROMISE = "更新后本区会换成";

describe("ProjectedLineupSection 不承诺产品兑现不了的状态", () => {
  it.each([null, "lastStarting11", "predicted", "standard", "confirmed"])(
    "lineupType=%s 时都不出现「更新后本区会换成已确认首发」这句承诺",
    (lineupType) => {
      const { container } = render(
        <ProjectedLineupSection {...BASE_PROPS} lineupType={lineupType} />,
      );
      expect(container.textContent).not.toContain(UNKEEPABLE_PROMISE);
    },
  );
});

describe("ProjectedLineupSection predicted/enetpulse 诚实标注为第三方预测", () => {
  it("lineup_type=predicted 时不得声称是「两队上一场的首发」", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" />,
    );
    expect(container.textContent).not.toContain("数据源给的是两队上一场的首发");
    expect(container.textContent).not.toContain("预计首发 · 基于上一场");
  });

  it("lineup_type=predicted 时文案标明这是预测/第三方来源", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" />,
    );
    expect(container.textContent).toMatch(/预测/);
  });

  it("lineup_type=lastStarting11 时仍然标注为「基于上一场」(这个是真的)", () => {
    render(<ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />);
    expect(screen.getAllByText("预计首发 · 基于上一场").length).toBeGreaterThan(0);
  });

  it("lineup_type 缺失/未知类型(standard 等)时不得冒充「上一场首发」", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="standard" />,
    );
    expect(container.textContent).not.toContain("数据源给的是两队上一场的首发");
  });
});

// ── 替补席三态(2026-08-18)──────────────────────────────────────────

describe("ProjectedLineupSection 替补席永不静默隐藏", () => {
  it.each([null, "lastStarting11", "predicted", "standard", "confirmed"])(
    "lineupType=%s 且 subs 为空时仍渲染「替补席」标题(用户投诉的回归钉)",
    (lineupType) => {
      const { container } = render(
        <ProjectedLineupSection {...BASE_PROPS} lineupType={lineupType} />,
      );
      expect(container.textContent).toContain("替补席");
    },
  );

  it("predicted + source=enetpulse + 空替补:显示 Enetpulse 专属说明,不显示通用说明,不折叠", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" source="enetpulse" />,
    );
    expect(container.textContent).toContain("这类预测名单只有首发 11 人，本来就不带替补。");
    expect(container.textContent).not.toContain("这次只拿到首发，没有替补。");
    // jsdom 不遵守 <details> 的折叠可见性,getByText 在折叠块里也找得到——
    // 空态是否真的没有套 <details> 必须断言 DOM 结构本身,不能只断言文本。
    expect(container.querySelector("details")).toBeNull();
  });

  it("predicted 但 source 不是 enetpulse(或缺失)时不得冒用 Enetpulse 的替补说法", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="predicted" source={null} />,
    );
    expect(container.textContent).not.toContain("这类预测名单只有首发 11 人");
    expect(container.textContent).toContain("这次只拿到首发，没有替补。");
  });

  it("lastStarting11 + 空替补:显示通用说明,不显示 Enetpulse 专属说明", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        source="lastStartingLineups"
      />,
    );
    expect(container.textContent).toContain("这次只拿到首发，没有替补。");
    expect(container.textContent).not.toContain("这类预测名单只有首发 11 人");
  });

  it("不得用另一条快照/另一场的替补顶替本条空替补", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />,
    );
    expect(container.textContent).not.toMatch(/上一场[^。]{0,20}替补|替补[^。]{0,20}上一场/);
  });

  it("有替补时正常渲染名单,且套着可折叠 <details>(默认折叠,不强制展开)", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={side({
          subs: [
            { id: 30, name: "替补甲", shirt_number: "20" },
            { id: 31, name: "替补乙", shirt_number: "21" },
          ],
        })}
      />,
    );
    expect(container.textContent).toContain("替补甲");
    expect(container.textContent).toContain("替补乙");
    expect(container.querySelector("details")).not.toBeNull();
  });
});

// ── 主教练(2026-08-18;2026-08-25 起两列并排,不再随 tab 互斥)─────────

describe("ProjectedLineupSection 主教练两列并排同屏", () => {
  it("双方主教练同时可见(tab 已移除,不再互斥)", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={side({ coach: { id: 1, name: "西蒙尼" } })}
        away={side({ team_id: 2, coach: { id: 2, name: "安切洛蒂" } })}
      />,
    );
    expect(container.textContent).toContain("主教练");
    expect(container.textContent).toContain("西蒙尼");
    expect(container.textContent).toContain("安切洛蒂");
    // 主/客切换 tab 已移除:除 <summary> 外不应再有任何按钮
    expect(container.querySelectorAll("button").length).toBe(0);
  });

  it("coach 缺失/为 null 时仍渲染「主教练」标签,诚实显示未包含说明,不留空、不编造", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />,
    );
    expect(container.textContent).toContain("主教练");
    expect(container.textContent).toContain("本条快照未包含主教练信息");
  });
});

// ── 空首发(2026-08-18,窗口放宽后从边缘情况变成常态)────────────────

describe("ProjectedLineupSection 空首发不再渲染出错误的门将/空球场", () => {
  it("单侧空首发:不出现「门将 」+空名字,渲染诚实的单侧空态说明", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={side({ starters: [], formation: null })}
      />,
    );
    expect(container.textContent).not.toMatch(/门将\s*。/);
    expect(container.textContent).toContain("这条快照里没有记录到主队的首发球员");
  });

  it("两侧皆空首发:渲染两侧皆空的说明,不渲染「数据源未标注这份名单的类型」", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType={null}
        home={side({ starters: [], formation: null })}
        away={side({ team_id: 2, starters: [], formation: null })}
      />,
    );
    expect(container.textContent).toContain("采集过这场比赛,但数据源当时还没有提供任何阵容名单");
    expect(container.textContent).not.toContain("数据源未标注这份名单的类型");
  });
});

describe("ProjectedLineupSection H7/H10:不断言假信息", () => {
  it("不得再声称「不含站位坐标」(H7,已证伪:fixture 里其实有 verticalLayout)", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />,
    );
    expect(container.textContent).not.toContain("不含站位坐标");
  });

  it("有快照但阵型缺失时队列头显示「阵型未知」不显示「无快照」(H10 语义延续)", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={side({ formation: null })}
      />,
    );
    const homeTitle = container.querySelector('[class*="teamTitle"]')!;
    expect(homeTitle.textContent).toContain("主队");
    expect(homeTitle.textContent).toContain("阵型未知");
    expect(homeTitle.textContent).not.toContain("无快照");
  });
});

describe("ProjectedLineupSection 整场都没有快照(此前生产常态,零覆盖)", () => {
  it("home/away 均为 null 时渲染整块空态,伤停显示「暂无数据」而不是「0 人」", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType={null} home={null} away={null} />,
    );
    expect(container.textContent).toContain("该场暂无阵容快照");
    expect(container.textContent).toContain("暂无数据");
    expect(container.textContent).toContain("该场暂无伤停快照采集记录");
    expect(container.textContent).not.toContain("0 人");
  });
});

describe("ProjectedLineupSection observed_at 按北京时间呈现(H11)", () => {
  it("显示「(北京时间)」后缀,不显示裸 UTC ISO 字符串", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />,
    );
    expect(container.textContent).toContain("(北京时间)");
    expect(container.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}T[\d:]+Z/);
  });
});

// ── 纵向双队球场(2026-08-25,共享 VerticalPitchFormation)──────────────

describe("纵向双队球场接入(预计首发,石板灰 probable 变体)", () => {
  /** id 故意乱序、且与真实站位相反(最小 id 分给前锋、最大 id 分给门将),
   * 坐标取自仓内真实 fixture(prematch-5104961.json,3-4-2-1)的真实观测值。 */
  const shuffledStarters = [
    { id: 5, name: "FW", shirt_number: "9", pos_x: 0.5, pos_y: 0.87 },
    { id: 40, name: "MID4", shirt_number: "8", pos_x: 0.875, pos_y: 0.485 },
    { id: 60, name: "DEF3", shirt_number: "6", pos_x: 0.79, pos_y: 0.292 },
    { id: 99, name: "GK", shirt_number: "1", pos_x: 0.5, pos_y: 0.1 },
    { id: 80, name: "AM2", shirt_number: "11", pos_x: 0.7, pos_y: 0.678 },
    { id: 20, name: "MID2", shirt_number: "4", pos_x: 0.125, pos_y: 0.485 },
    { id: 10, name: "DEF1", shirt_number: "2", pos_x: 0.21, pos_y: 0.292 },
    { id: 50, name: "MID3", shirt_number: "7", pos_x: 0.625, pos_y: 0.485 },
    { id: 15, name: "AM1", shirt_number: "10", pos_x: 0.3, pos_y: 0.678 },
    { id: 70, name: "DEF2", shirt_number: "5", pos_x: 0.5, pos_y: 0.292 },
    { id: 30, name: "MID1", shirt_number: "3", pos_x: 0.375, pos_y: 0.485 },
  ];

  it("有坐标时画竖版整场(viewBox 0 0 68 105)+ probable 灰场变体,主/客 tab 不存在", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={{ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters: shuffledStarters }}
      />,
    );
    const svg = container.querySelector("svg");
    // 2026-08-25 半场改纵向整场(两队同屏,FotMob 恒纵向)
    expect(svg?.getAttribute("viewBox")).toBe("0 0 68 105");
    expect(container.querySelector('[data-variant="probable"]')).not.toBeNull();
    expect(container.querySelectorAll("button").length).toBe(0);
    // 门将与前锋都在球场上(标签为"球衣号 姓名")
    expect(screen.getByText("1 GK")).not.toBeNull();
    expect(screen.getByText("9 FW")).not.toBeNull();
  });

  it("旧快照(无站位坐标)时不画球场,各队列内退化为纯名单——不猜站位", () => {
    const starters = shuffledStarters.map((p) => ({ ...p, pos_x: null, pos_y: null }));
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={{ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters }}
        away={null}
      />,
    );
    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toContain("这份名单没带坐标");
    // 首发在主队列里按纯名单列出
    expect(screen.getByText("1 GK")).not.toBeNull();
  });

  it("球场标记是真实球员头像(img),加载失败时回退成球衣号文字而不是裂图标", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={{ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters: shuffledStarters }}
        away={null}
      />,
    );
    const images = container.querySelectorAll('[data-testid="player-avatar-image"] img');
    expect(images.length).toBe(shuffledStarters.length);
    const gkImage = screen.getByText("1 GK").parentElement!.querySelector("img")!;
    fireEvent.error(gkImage);
    expect(screen.getByText("1")).not.toBeNull(); // 回退成球衣号文字
    expect(container.querySelectorAll('[data-testid="player-avatar-image"] img').length).toBe(
      shuffledStarters.length - 1,
    );
  });
});
