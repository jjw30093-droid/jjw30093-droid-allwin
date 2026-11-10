import type { Metadata } from "next";
import { AnalysisIndex } from "@/components/analysis/AnalysisReader";
export const metadata:Metadata={title:"足球研究",description:"喵弟足球研究：赛前研究、赛后复盘与整轮观察。"};
export default function Page(){return <AnalysisIndex/>}
