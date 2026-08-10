# Design Review: Crayotter 线上工作台

Reviewed against: 无 DESIGN_BRIEF.md（以线上产品自身为基准）
Philosophy: 简洁现代 SaaS 工作台（侧栏导航 + 卡片式内容 + 紫蓝主色）
Date: 2026-08-10
URL: http://8.161.229.68/ui/

## Screenshots Captured

| Screenshot | Breakpoint | Description |
|---|---|---|
| `screenshots/review-login-desktop-1280.png` | Desktop | 登录页 |
| `screenshots/review-workbench-desktop-1280.png` | Desktop | 工作台（hero + 三阶段 + composer） |
| `screenshots/review-workbench-tablet-768.png` | Tablet | 工作台（右侧统计栏被挤压） |
| `screenshots/review-workbench-mobile-375.png` | Mobile | 工作台（hero 文字竖排、卡片重叠） |
| `screenshots/review-jobs-desktop-1280.png` | Desktop | 任务历史 |
| `screenshots/review-jobs-tablet-768.png` | Tablet | 任务历史空状态 |
| `screenshots/review-jobs-mobile-375.png` | Mobile | 任务历史空状态 |
| `screenshots/review-materials-desktop-1280.png` | Desktop | 素材库（免责声明 + 工具栏） |
| `screenshots/review-materials-tablet-768.png` | Tablet | 素材库 |
| `screenshots/review-materials-mobile-375.png` | Mobile | 素材库（自适应良好） |
| `screenshots/review-composer-options-desktop-1280.png` | Desktop | 创作选项弹层（左缘裁切） |

> 全部位于 `.design/workbench/screenshots/`。

## Summary

桌面端整体质量良好：视觉层次清晰、空状态有引导、新增功能（免责声明/批量/通知）融入自然。最大问题集中在**响应式**：工作台在 ≤768px 时 hero 与右侧统计栏布局彻底破坏（文字竖排、卡片重叠），属于线上可见的阻断级缺陷；创作选项弹层在桌面也有定位裁切问题。

## Must Fix

1. **移动端工作台 hero 布局崩溃**：375px 下「你好，我已经准备好」逐字竖排成一列，「当前任务」标签也竖排，三张统计卡（任务状态/当前阶段/日志事件）浮在 hero 之上互相重叠。见 `review-workbench-mobile-375.png`。_Fix: hero 容器在窄屏应有 `min-width: 0` + `flex-direction: column`，文字块去掉固定宽度/`writing-mode` 诱因（大概率是 flex 子项未设 `min-width:0` 导致被压缩到一字宽）；统计栏在 <768px 应改为横排三列或折叠到 hero 下方。_
2. **平板端右侧统计栏挤压竖排**：768px 下三张统计卡每张仅 ~90px 宽，「任务状态」「待启动」等文字逐字换行，不可读。见 `review-workbench-tablet-768.png`。_Fix: 768–1024px 区间应让右栏改为横向三卡片条（grid 3 列）置于 hero 下方，而不是保留窄侧栏。_
3. **创作选项弹层左缘被裁切**：桌面端弹层向上展开时左边缘超出 composer 容器，标题「创作选项」显示为「作选项」，第一行说明文字也被裁。见 `review-composer-options-desktop-1280.png`。_Fix: 弹层定位加边界检测（或 `left: 0` 对齐触发按钮而非溢出），并检查与 phase tracker 的 z-index/重叠关系（弹层半透明压在「成片执行」卡上）。_

## Should Fix

1. **移动端 composer 操作区拥挤**：375px 下「创作选项」「演示模式」两个下拉 + 发送按钮挤一行，触摸目标 <44px。_Fix: 窄屏时选项区换行，发送按钮保持 ≥44px。_
2. **登录页无品牌识别**：纯白卡片只有文字标题，无 logo/产品视觉，与工作台的 Crayotter 品牌（logo + AI Video Studio）断层。见 `review-login-desktop-1280.png`。_Fix: 登录卡顶部加 logo + 产品名。_
3. **任务历史空状态描述冗余**：「还没有历史任务。」下面重复一行「查看、选择和管理历史视频任务。」（与页头副标题相同）。_Fix: 空状态副文案改为行动引导，如「创建你的第一个视频任务」。_
4. **素材库移动端头部按钮换行**：「上传素材」「上传大文件」两按钮竖排占两行，挤压标题区。_Fix: 窄屏合并为一个「上传」下拉（小文件/大文件两项）。_

## Could Improve

1. **Hero 装饰图标**：桌面端 hero 右侧圆形魔术棒图标偏弱，可考虑与工作流状态联动（运行中动画）。
2. **三阶段卡片**：待启动状态视觉完全一致，可在当前阶段卡片上加主色边框强化「进行到哪一步」的扫描感。
3. **底部移动导航**：已有底部 tab 栏（良好），但无 safe-area padding 处理，iOS 底部横条可能遮挡。

## What Works Well

- 素材库免责声明条（琥珀色）醒目且不突兀，文案合规完整（`review-materials-*`）。
- 移动端素材库整体自适应良好：搜索框、筛选下拉、空状态均正确换行。
- 桌面端工作台视觉层次清晰：hero → 阶段进度 → composer 阅读顺序自然，侧栏导航状态明确。
- 空状态均有图标 + 标题 + 引导按钮（任务历史「创建新任务」），符合规范。
- 创作选项弹层内容结构（分组 + 勾选态 + 免责声明小字）信息设计合理。

## 下一步开发建议（按优先级）

1. **修复工作台响应式布局**（Must Fix 1/2/3）——一个 CSS-focused 迭代即可覆盖，配合三端截图回归。
2. **登录页品牌化 + 空状态文案优化**（Should 2/3）——小改动，提升首因效应。
3. **移动端 composer 体验**（Should 1/4）——合并上传入口、放大触摸目标。
4. 之后再考虑 Could 项的视觉打磨。
