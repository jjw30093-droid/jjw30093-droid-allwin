/**
 * ISR 页面取数失败时"保留上一版正常页面"(2026-10-02 线上实测:发版重启接口的
 * 几十秒里首页恰好重新生成,把"今日比赛暂时无法加载"当成正常结果缓存下来,
 * 抖音来的新用户第一眼看到的就是报错)。
 *
 * Next ISR 的规则:重新生成时抛错,会继续用上一份成功生成的页面,下一个请求再重试
 * (node_modules/next/dist/docs/01-app/02-guides/incremental-static-regeneration.md)。
 * 所以生产运行时取数失败要**抛出去**,不能吞成空结果;只有构建期(没有上一版可用)
 * 和开发/测试环境才退回降级内容。
 *
 * 只用于 ISR 页面(首页)。动态页面用它会把降级内容变成整页报错。
 */
export function keepLastGoodPage<T>(fallback: T): (err: unknown) => T {
  return (err) => {
    if (process.env.NODE_ENV === "production" && process.env.NEXT_PHASE !== "phase-production-build") {
      throw err;
    }
    return fallback;
  };
}
