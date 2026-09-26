/**
 * 榜单分组 DOM id 的统一写法。放在不带 "use client" 的独立文件里:服务端组件
 * (TeamStatsSections / PlayerBoards)要调用它,而 BoardSectionTabs 是客户端组件——
 * 客户端文件里导出的函数在服务端不能调用(CLAUDE.md §11.4)。
 */
export const boardSectionDomId = (id: string) => `board-${id}`;
