import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SampleRow, type Sample } from "@/app/track-record/page";

afterEach(cleanup);

function baseSample(overrides: Partial<Sample> = {}): Sample {
  return {
    snapshot_id: "snap-1",
    match_id: 1,
    kickoff_at_utc: "2026-08-07T17:00:00Z",
    home: { team_id: 1, name: "主队", name_en: "Home", crest_url: null },
    away: { team_id: 2, name: "客队", name_en: "Away", crest_url: null },
    home_probability: 0.5,
    draw_probability: 0.3,
    away_probability: 0.2,
    predicted_outcome: "home",
    actual_outcome: null,
    home_goals: null,
    away_goals: null,
    hit: null,
    status: "locked",
    superseded_by: null,
    correction_of: null,
    superseded_note: null,
    edit_count: 0,
    last_edited_at: null,
    model_version_id: "m-test",
    published_at: "2026-08-01T00:00:00Z",
    ...overrides,
  } as Sample;
}

function renderRow(s: Sample) {
  return render(
    <table>
      <tbody>
        <SampleRow s={s} />
      </tbody>
    </table>,
  );
}

describe("SampleRow 修正徽标(2026-08-05:预测可编辑,修正留痕公开可查)", () => {
  it("edit_count=0 时不显示修正徽标", () => {
    renderRow(baseSample({ edit_count: 0 }));
    expect(screen.queryByText(/已修正/)).toBeNull();
  });

  it("edit_count>0 时显示修正次数与最近修正时间", () => {
    renderRow(baseSample({ edit_count: 2, last_edited_at: "2026-08-05T10:00:00Z" }));
    expect(screen.getByText(/已修正 2 次/)).not.toBeNull();
  });

  it("edit_count>0 但 last_edited_at 缺失时仍显示次数,不崩溃", () => {
    renderRow(baseSample({ edit_count: 1, last_edited_at: null }));
    expect(screen.getByText(/已修正 1 次/)).not.toBeNull();
  });

  it("已撤回样本仍然可以同时显示修正徽标(两者独立,不互斥)", () => {
    renderRow(baseSample({ status: "retracted", edit_count: 1, last_edited_at: "2026-08-05T10:00:00Z" }));
    expect(screen.getByText("已撤回")).not.toBeNull();
    expect(screen.getByText(/已修正 1 次/)).not.toBeNull();
  });
});
