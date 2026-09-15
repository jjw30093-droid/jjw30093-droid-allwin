/**
 * 象限图视角注册表纪律(2026-09-14):新增视角容易漏掉的几件事——分组登记、
 * 象限名齐全、x 反向要有说明、不能冒充 Field Tilt/PPDA、定位球占比不能
 * 写成"占总 xG"。这里一次性守住,不用每加一个视角都人工过一遍。
 */

import { describe, expect, it } from "vitest";
import { METRICS, type MetricDef } from "@/components/league/teamMetrics";
import { VIEWS, VIEW_GROUPS, groupedViews, viewById } from "@/components/league/quadrantViews";

describe("视角注册表", () => {
  it("id 唯一", () => {
    const ids = VIEWS.map((v) => v.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("每个视角的 group 都在 VIEW_GROUPS 里", () => {
    const groupIds = new Set(VIEW_GROUPS.map((g) => g.id));
    for (const v of VIEWS) {
      expect(groupIds.has(v.group)).toBe(true);
    }
  });

  it("groupedViews() 里每一组都非空(还没有视角的类别不出现)", () => {
    for (const { views } of groupedViews()) {
      expect(views.length).toBeGreaterThan(0);
    }
  });

  it("四象限名齐全、互不相同", () => {
    for (const v of VIEWS) {
      expect(v.quadrants.length).toBe(4);
      expect(v.quadrants.every((q) => q.trim().length > 0)).toBe(true);
      expect(new Set(v.quadrants).size).toBe(4);
    }
  });

  it("x 轴反向(lowerIsBetter)的视角,note 必须写明从右往左读", () => {
    for (const v of VIEWS) {
      if (v.x.lowerIsBetter) {
        expect(v.note).toMatch(/越靠左|从右往左|反转/);
      }
    }
  });

  it("viewById 找不到时抛错,不静默返回 undefined", () => {
    expect(() => viewById("does-not-exist")).toThrow();
    expect(viewById("both-ends").id).toBe("both-ends");
  });

  it("没有任何轴标签把自己叫做 Field Tilt 或 PPDA(命名红线)", () => {
    // 轴名本身不能是 Field Tilt/PPDA;但视角说明里提"这不是官方 Field Tilt"
    // 这种否定式免责声明是允许的、甚至是要求的(照抄
    // backend/metrics/registry.py 的 opp_half_pass_share 红线),不能把
    // "出现这几个字"当成红线本身——红线是"敢不敢自称",不是"敢不敢提及"。
    for (const v of VIEWS) {
      expect(v.x.label).not.toMatch(/Field Tilt|PPDA/);
      expect(v.y.label).not.toMatch(/Field Tilt|PPDA/);
      if (v.note.includes("Field Tilt")) {
        expect(v.note).toMatch(/不是.*Field Tilt|代理指标/);
      }
    }
  });

  it("METRICS 里没有任何指标 label 冒充 Field Tilt/场地倾斜", () => {
    for (const m of Object.values(METRICS)) {
      expect(m.label).not.toMatch(/Field Tilt|场地倾斜/);
    }
  });

  it("定位球相关指标不正面声称'占总 xG'(口径已定为占运动战+定位球 xG 之和)", () => {
    // 与 Field Tilt 红线同一模式:文案里出现"不是「占总 xG」"这种否定式
    // 免责声明是允许的、甚至是要求的,红线是"敢不敢正面这么叫",不是
    // "敢不敢提及这四个字"。label 本身(轴上短名/详情面板行名)必须干净,
    // caliber 若提及必须是否定句。
    for (const raw of Object.values(METRICS)) {
      const m = raw as MetricDef;
      if (m.id.includes("set_piece") || m.label.includes("定位球")) {
        expect(m.label).not.toContain("占总 xG");
        if ((m.caliber ?? "").includes("占总 xG")) {
          expect(m.caliber).toMatch(/不(是|含).{0,4}占总 xG/);
        }
      }
    }
  });

  it("「终结记录」视角的四象限名不出现'终结能力'字样(方案护栏:调研认为该指标跨赛季相关性近零,不得暗示这是可持续能力)", () => {
    const v = viewById("finishing-record");
    for (const q of v.quadrants) {
      expect(q).not.toContain("终结能力");
    }
  });

  it("outcome_variance 语义的轴,caliber 里必须能看出'不是稳定能力/短期窗口'这层意思(与后端 backend/metrics/registry.py 同一条红线)", () => {
    for (const raw of Object.values(METRICS)) {
      const m = raw as MetricDef;
      if (m.semantic === "outcome_variance") {
        expect(m.caliber ?? "").toMatch(/短期|不是稳定|近期记录/);
      }
    }
  });

  it("有 outcome_variance 轴的视角都能在 VIEWS 里找到(防止手滑漏挂 group/未登记)", () => {
    const outcomeVarianceViewIds = VIEWS.filter(
      (v) => v.x.semantic === "outcome_variance" || v.y.semantic === "outcome_variance",
    ).map((v) => v.id);
    expect(outcomeVarianceViewIds).toEqual(expect.arrayContaining(["finishing-record", "defence-goalkeeping"]));
  });
});
