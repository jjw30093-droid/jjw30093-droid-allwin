/**
 * ProjectedLineupSection 诚实文案测试(PIPELINE_REDESIGN_V2 P2 + 2026-08-18
 * 阵容采集窗口 72h + 主教练 + 替补诚实空态)。
 *
 * 历史两个真实缺陷(P2,保留):
 * 1. 组件原来无条件承诺"更新后本区会换成「已确认首发」"——但真实抓取
 *    228 行 bronze_fm_lineup_snap 里 lineup_type 从未出现过 "confirmed",
 *    这是一个产品永远兑现不了的承诺(CLAUDE.md §2.2 禁止编造能力)。
 * 2. lineup_type="predicted"(source="enetpulse",16 行真实数据)被无条件
 *    渲染成"数据源给的是两队上一场的首发"——但 Enetpulse 的 predicted 是
 *    第三方对本场比赛的预测阵容,不是上一场的真实首发,这是一处独立的
 *    事实性错误,不是"确认/未确认"这一个维度能覆盖的。
 *
 * 2026-08-18 新增覆盖(生产 99 场未来比赛 0 条阵容快照的根因之一是采集窗口
 * 太窄——修完窗口后,组件本身还有三处独立缺陷:替补为空时静默隐藏整块、
 * 空首发渲染出"门将 "+空球场、球场图注文案断言了假的"不含站位坐标"和
 * 一个排序算法保证不了的门将姓名):
 * - 替补三态(有替补/predicted+enetpulse 无替补/其它类型无替补)，空态绝不
 *   折叠(必须断言 DOM 里没有 <details>,不能只断言文本——jsdom 不遵守
 *   <details> 的可见性,getByText 在折叠块里也找得到);
 * - 主教练随主/客 tab 切换(使用 fireEvent,不用 userEvent——
 *   @testing-library/user-event 不在依赖里,见 frontend/tests/
 *   admin-access-tab.test.tsx 的既有点击测试同样用 fireEvent);
 * - 单侧空首发 / 两侧皆空两种此前零覆盖的状态(今天生产 100% 的比赛是
 *   home:null,away:null,却没有任何测试断言过这个状态长什么样);
 * - H7(不含站位坐标)与 H8(门将 {错误的人})两处已确认的假文案被钉死
 *   不能再出现。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ProjectedLineupSection, rowsFor } from "@/components/matches/ProjectedLineupSection";
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
    // gate 必须是 source==="enetpulse",不能是 lineupType==="predicted"——
    // vendor 名是 source 的属性,哪天换了预测供应商但 lineup_type 仍是
    // "predicted",用 lineup_type 判定就是对用户说假话。注意:notice 文案本身
    // (「数据源标注为第三方(Enetpulse)对本场比赛的预测阵容」)是既有 P2 行为、
    // 与 lineup_type==="predicted" 挂钩,不在本次任务范围内——这里只钉替补
    // 空态那句话是否正确按 source 分支,不断言整页不出现"Enetpulse"字样。
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
    // 用有界距离而不是无界 .*——页面上"上一场"(来自 tag/pitchCaption,描述
    // 首发名单的性质)与"替补"(来自替补空态说明)本来就会各自合法地出现在
    // 页面上,无界正则会把两处无关文字误判为"声称替补来自上一场"。
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

// ── 主教练(2026-08-18)──────────────────────────────────────────────

describe("ProjectedLineupSection 主教练随主/客 tab 切换", () => {
  it("每侧渲染各自的主教练,且切换 tab 后正文随之切换", () => {
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
    expect(container.textContent).not.toContain("安切洛蒂");

    // 不用 screen.getByText("客队")——「客队」同时出现在伤停卡片标题里,
    // 文本查询是歧义的。主/客 tab 是本组件唯一的两个 <button>,且顺序固定
    // (主队在前),用结构定位比用文本定位更稳。
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(2);
    fireEvent.click(buttons[1]);

    expect(container.textContent).toContain("安切洛蒂");
    expect(container.textContent).not.toContain("西蒙尼");
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

describe("ProjectedLineupSection H7/H8 止损:不再断言假的坐标/门将信息", () => {
  it("不得再声称「不含站位坐标」(H7,已证伪:fixture 里其实有 verticalLayout)", () => {
    const { container } = render(
      <ProjectedLineupSection {...BASE_PROPS} lineupType="lastStarting11" />,
    );
    expect(container.textContent).not.toContain("不含站位坐标");
  });

  it("tab 标签:有快照但阵型缺失时不得显示「无快照」(H10,与 disabled 状态自相矛盾)", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={side({ formation: null })}
      />,
    );
    // 主队 tab 应显示"阵型未知"而不是"无快照"——home 本身是有效快照对象。
    // 不用 screen.getByText("主队")——「主队」同时出现在伤停卡片标题里,
    // 文本查询是歧义的;两个 tab 按钮里主队固定在前。
    const homeTab = container.querySelectorAll("button")[0];
    expect(homeTab.textContent).not.toContain("无快照");
    expect(homeTab.textContent).toContain("阵型未知");
  });
});

describe("ProjectedLineupSection 整场都没有快照(生产 100% 状态,此前零覆盖)", () => {
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

describe("rowsFor E-full:按真实球场坐标分行分列(H8 真修复,不再是止损)", () => {
  /** id 故意乱序、且与真实站位相反(最小 id 分给前锋、最大 id 分给门将),
   * 防止实现"偷懒"继续依赖 id 排序侥幸凑对——必须真的按 pos_y/pos_x 分行。
   * 坐标取自仓内真实 fixture(prematch-5104961.json,formation 3-4-2-1)的
   * 真实观测值。 */
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

  it("按 pos_y 分行(门将永远是 y 最小的那个,不是数组第 0 个)、行内按 pos_x 从左到右排", () => {
    const rows = rowsFor({
      team_id: 1, formation: "3-4-2-1", coach: null, subs: [],
      starters: shuffledStarters,
    });
    expect(rows?.map((r) => r.map((p) => p.id))).toEqual([
      [99],
      [10, 70, 60],
      [20, 30, 50, 40],
      [15, 80],
      [5],
    ]);
  });

  it("任一首发缺 pos_x/pos_y(旧快照)时返回 null,不按错误顺序猜测摆位", () => {
    const starters = shuffledStarters.map((p, i) =>
      i === 0 ? { ...p, pos_x: null, pos_y: null } : p,
    );
    const rows = rowsFor({ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters });
    expect(rows).toBeNull();
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

describe("球场底图(2026-08-20,参照 miaomiaodi.cc:真实球场标记替换旧的 4 个装饰 span)", () => {
  /** 复用上面 rowsFor 测试组同一份真实坐标 fixture——有真实坐标时才会真的
   * 画球场(否则退化成纯名单),这里同时确认 FootballPitchBackground 以
   * orientation="portrait" 接入,而不是误用了 landscape 默认值。 */
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

  it("有真实站位坐标时画竖版真实半场(viewBox 0 52.5 68 52.5),不再是旧的 4 个装饰 span", () => {
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={{ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters: shuffledStarters }}
      />,
    );
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // 2026-08-20 由全场改半场:球场另一端从来没有球员站在那,画出来是纯装饰。
    expect(svg?.getAttribute("viewBox")).toBe("0 52.5 68 52.5");
    // 旧的装饰 span 类名不应再出现
    expect(container.querySelector('[class*="pitchLine"]')).toBeNull();
    expect(container.querySelector('[class*="pitchCircle"]')).toBeNull();
    expect(container.querySelector('[class*="pitchBox"]')).toBeNull();
    // 门将(id=99,pos_y 最小)与前锋(id=5,pos_y 最大)都真实渲染在球场上
    expect(screen.getByText("GK")).not.toBeNull();
    expect(screen.getByText("FW")).not.toBeNull();
  });

  it("旧快照(无站位坐标)时不画球场,退化为纯名单——不应误画一个错位的球场", () => {
    const starters = shuffledStarters.map((p) => ({ ...p, pos_x: null, pos_y: null }));
    const { container } = render(
      <ProjectedLineupSection
        {...BASE_PROPS}
        lineupType="lastStarting11"
        home={{ team_id: 1, formation: "3-4-2-1", coach: null, subs: [], starters }}
      />,
    );
    expect(container.querySelector("svg")).toBeNull();
  });
});
