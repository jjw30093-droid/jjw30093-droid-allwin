import { redirect } from "next/navigation";

// /league/{id} 本身不是页面——联赛内容按栏目(积分榜/赛程/球员/球队数据)
// 拆分在子路由下,这一层此前没有 page.tsx,合法的联赛 id 只是没带栏目就会
// 被当成路由不存在而 404。这不是"链接坏了",是"链接省了默认栏目",所以
// 重定向到积分榜而不是走 404 那一套。id 是否真实存在由 standings 页自己
// 的取数逻辑处理。
export default async function LeagueRedirectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/league/${id}/standings`);
}
