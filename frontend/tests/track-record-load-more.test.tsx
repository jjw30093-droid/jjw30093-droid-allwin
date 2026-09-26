/**
 * 精选页「历史战绩」:默认显示最近 10 条,底部「加载更多」每次 10 条(2026-09-26)。
 * /track-record 与 /reco?tab=record 共用 TrackRecordPanel,所以两个入口行为一致。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TrackRecordPanel, type Slip } from "@/components/reco/TrackRecordPanel";

afterEach(cleanup);

function slip(i: number): Slip {
  return {
    id: `s${i}`,
    slip_date: "2026-09-01",
    title: `战绩单${i}`,
    note: null,
    combo_type: "single",
    status: "settled",
    result: "win",
    return_units: 1.9,
    published_at: "2026-09-01T00:00:00Z",
    settled_at: "2026-09-01T20:00:00Z",
    edit_count: 0,
    last_edited_at: "2026-09-01T20:00:00Z",
    legs: [
      { id: `l${i}`, match_id: 9000 + i, match_desc: "主队 vs 客队", market: "1x2", selection: "主胜", odds: 1.9, result: "win" },
    ],
  } as Slip;
}

describe("TrackRecordPanel 加载更多", () => {
  it("25 条战绩:默认 10 条,加载更多 → 20 → 25,最后按钮消失;标题里的总数仍是 25", () => {
    const slips = Array.from({ length: 25 }, (_, i) => slip(i + 1));
    render(<TrackRecordPanel summary={null} slips={slips} total={25} />);
    expect(screen.getByText("战绩归档（25）")).toBeTruthy();
    expect(screen.getAllByText(/^战绩单\d+$/)).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getAllByText(/^战绩单\d+$/)).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getAllByText(/^战绩单\d+$/)).toHaveLength(25);
    expect(screen.queryByRole("button", { name: /加载更多/ })).toBeNull();
  });

  it("10 条以内不出现「加载更多」", () => {
    const slips = Array.from({ length: 10 }, (_, i) => slip(i + 1));
    render(<TrackRecordPanel summary={null} slips={slips} total={10} />);
    expect(screen.getAllByText(/^战绩单\d+$/)).toHaveLength(10);
    expect(screen.queryByRole("button", { name: /加载更多/ })).toBeNull();
  });
});
