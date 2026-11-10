import { AnalysisArticle } from "@/components/analysis/AnalysisReader";
export default async function Page({params}:{params:Promise<{slug:string}>}){const {slug}=await params;return <AnalysisArticle key={slug} slug={slug}/>}
