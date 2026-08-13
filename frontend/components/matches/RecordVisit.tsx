"use client";

/** 详情页挂载即把比赛记入「最近浏览」(本机 localStorage),不渲染任何内容。 */

import { useEffect } from "react";
import { recordViewed } from "@/lib/recently-viewed";

export function RecordVisit({ matchId }: { matchId: number }) {
  useEffect(() => {
    recordViewed(matchId);
  }, [matchId]);
  return null;
}
