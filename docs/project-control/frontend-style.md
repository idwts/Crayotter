# 前端风格表

> 说明：本表记录 Crayotter 前端的设计系统、组件清单与复用约定，便于后续新增页面（登录/注册/账号/管理后台）时保持风格统一。智能体阅读后应记住：**前端基于 React + Tailwind CSS，2026-08-11 全量色彩归一后主色调为靛蓝 `#5E6FEF`（品牌文字色 `#5266E9`），背景为 `#F4F7FA`，全站唯一色板定义在 `styles.css` 顶部 `:root` 的 `--app-*` CSS 变量中，新增样式必须使用这些变量，禁止新增一次性硬编码色值。**

## 1. 技术栈

| 项目 | 说明 |
|------|------|
| 框架 | React 18（JSX） |
| 构建工具 | Vite |
| 样式 | Tailwind CSS + 自定义 `styles.css` |
| 图标 | `lucide-react` |
| 国际化 | 自研 `i18n.js`（`MESSAGES` 对象） |
| 状态 | `useState`/`useRef`/`useCallback` + `localStorage` |

## 2. 设计 Token（2026-08-11 全量归一）

全站唯一色板：`styles.css` 顶部 `:root` 的 `--app-*` 变量（CSS 侧唯一事实源），`tailwind.config.js` 的 `app.*` 色值已与之对齐（JSX 侧）。**新增颜色必须先查此表，语义对不上时才允许新增 token 并登记本表。**

| Token | 值 | 用途 |
|-------|-----|------|
| `app.bg` | `#F4F7FA` | 页面背景 |
| `app.surface` | `#FFFFFF` | 卡片/浮层面板 |
| `app.panel` | `#F8FAFC` | 侧边栏/次级面板 |
| `app.fill` | `#EDF0F6` | 芯片/输入框/弱填充（仅 CSS） |
| `app.line` | `#D9E1EA` | 分割线/边框 |
| `app.ink` / `app.ink-2` | `#101828` / `#334155` | 主文字 / 次级标题 |
| `app.soft` / `app.muted` / `app.disabled` | `#667085` / `#98A2B3` / `#CBD3E4` | 辅助文字 / 占位 / 禁用 |
| `app.brand` / `app.brand-ink` | `#5E6FEF` / `#5266E9` | 主操作 / 品牌文字链接 |
| `app.brand-soft` / `app.brand-line` / `app.brand-focus` / `app.brand-dark` | `#EEF1FF` / `#E0E7FF` / `#9AA5F2` / `#2D3261` | 选中浅底 / 品牌浅边 / 聚焦环 / 阶段完成深底 |
| `app.success` / `-ink` / `-soft` | `#22C55E` / `#166534` / `#DCFCE7` | 成功状态 |
| `app.danger` / `-soft` / `-line` | `#D92D20` / `#FFF1F2` / `#FFEBED` | 危险/删除 |
| `app.warning-ink` / `-soft` | `#92590A` / `#FFF8EC` | 警示条（免责声明等；tailwind `app.warning` `#DC6803` 仅 JSX 用） |
| 类别色（非语义） | `app.sky` `#3B82F6` / `app.sky-soft` `#EFF6FF` / `app.violet` `#8463D9` / `app.violet-soft` `#EDE9FE` | metric 卡片等分类标识 |
| 深色预览面 | `app.dark` `#0B1220` / `app.dark-2` `#222B3D` | 视频播放器/产物预览 |

圆角刻度：`6px`（小元素）/ `8px`（按钮、输入框，主刻度）/ `10px` / `12px` / `16px`（卡片、弹层）/ `999px`（胶囊）。禁止 5/7/9/14px 等刻度外取值。

## 3. 自定义 CSS 关键类（styles.css）

阅读 `app/frontend_src/src/styles.css` 后总结的核心复用类：

| 类名 | 说明 | 使用场景 |
|------|------|----------|
| `.app-sidebar` | 左侧导航栏 | 工作台主布局 |
| `.nav-item` / `.nav-item-active` | 导航项/选中态 | 侧边栏菜单 |
| `.icon-button` | 图标按钮 | 工具栏、关闭按钮 |
| `.primary-button` | 主操作按钮 | 创建任务、保存 |
| `.secondary-button` | 次级按钮 | 取消、返回 |
| `.danger-button` | 危险按钮 | 删除、取消任务 |
| `.toast-viewport` / `.toast-item` | Toast 通知 | 全局反馈 |
| `.dialog-layer` / `.dialog-backdrop` | 模态框层级 | 确认对话框、设置 |
| `.confirm-dialog` | 确认对话框 | 删除确认 |
| `.settings-modal` | 设置模态框 | 系统配置 |
| `.motion-enter` | 进入动画 | 弹窗 |
| `.materials-search-input` | 素材库搜索输入框（flex:1，聚焦 `--app-brand`（#5E6FEF）） | 素材库工具栏（2026-08-04 新增） |
| `.materials-toolbar-select` | 素材库筛选/排序下拉 | 素材库工具栏（2026-08-04 新增） |
| `.materials-batch-bar` | 批量选择工具条（全选复选框 + 删除选中按钮） | 素材库列表上方（2026-08-06 新增） |
| `.materials-select-all` | 全选标签样式 | 批量工具条 |
| `.materials-batch-delete` | 删除选中按钮 | 批量工具条 |
| `.material-select` | 每行素材复选框 | 素材行 |
| `.material-row.selected` | 选中态背景/边框 | 素材行 |
| `.material-preview-layer` | 视频预览弹层遮罩层（z-index 70） | 素材库点击「预览」后（2026-08-06 新增） |
| `.material-preview-dialog` | 视频预览容器（深色背景、圆角、阴影） | 预览弹层 |
| `.material-preview-video` | `<video>` 元素样式 | 预览弹层 |

## 4. 组件清单

### 4.1 页面级组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `App` | `main.jsx` | 根组件，包含路由视图、全局状态、请求封装、登录态管理（`authUser`/`authView`）与服务端偏好同步（登录后 GET `/api/auth/preferences` 应用语言/侧栏/视图/任务草稿等，状态变更 1s 防抖 POST 回写，`prefsApplyingRef` 防回环） |
| `WorkbenchView` | `DashboardUI.jsx` | 工作台主视图（三栏布局） |
| `JobsView` | `DashboardUI.jsx` | 任务列表视图 |
| `MaterialsView` | `DashboardUI.jsx` | 素材库视图 |
| `ArtifactsView` | `DashboardUI.jsx` | 产物库视图 |
| `LoginPage` | `AuthPages.jsx` | 登录页（含"忘记密码？"入口、"记住我（30 天免登录）"与"记住用户名"复选框；记住用户名存 localStorage `crayotter.rememberedUsername`，密码永不落盘） |
| `RegisterPage` | `AuthPages.jsx` | 注册页（成功后展示一次性恢复码；2026-08-02 起注册成功自动建立会话直接进入工作台） |
| `ResetPasswordPage` | `AuthPages.jsx` | 恢复码重置密码页 |

### 4.1a 认证页复用元素（AuthPages.jsx）

| 元素 | 说明 |
|------|------|
| `Field` | 表单字段组件：label + `.input` 类名（无专用样式规则，实际外观由 styles.css:130 的元素级 `input, textarea` 选择器提供）+ 可选密码可见性切换（Eye/EyeOff）+ 错误提示（`text-app-danger`） |
| `cx` | 类名拼接工具（filter(Boolean).join） |
| 布局约定 | `grid min-h-screen place-items-center bg-app-bg p-4` 居中容器 + `max-w-md rounded-2xl bg-app-surface p-8 shadow-sm` 卡片 |
| 提交按钮 | `.primary-button flex w-full items-center justify-center gap-2` + `Loader2` 旋转动画（busy 态） |
| 切换链接 | `text-app-brand hover:underline` 文字按钮（登录/注册/重置互跳） |
| 品牌 | `brand-mascot.png` 16×16 居中置于标题上方 |

### 4.2 布局组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `SettingsModal` | `SettingsModal.jsx` | 设置弹窗；公开模式下“API 密钥”页签变为“API 来源”二选一（平台配额/我的 API）+ BYOK 表单（密钥留空=保持不变，掩码占位提示），高级页签在公开模式隐藏；登录用户可见「账号安全」页签（`ChangePasswordSection`，2026-08-11 新增：原密码+新密码+确认，成功后全会话失效回登录页） |
| `AppSidebar` | `DashboardUI.jsx` | 左侧导航栏，支持折叠 |
| `AppTopbar` | `DashboardUI.jsx` | 顶部工具栏 |
| `MobileDrawer` | `DashboardUI.jsx` | 移动端抽屉导航 |
| `MobileBottomNav` | `DashboardUI.jsx` | 移动端底部导航 |
| `ContextPanel` | `DashboardUI.jsx` | 右侧详情/检查器面板 |

### 4.3 任务/素材组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `TaskHero` | `DashboardUI.jsx` | 任务主信息卡片 |
| `OverviewStrip` | `DashboardUI.jsx` | 任务统计条 |
| `PhaseTracker` | `DashboardUI.jsx` | 三阶段进度追踪 |
| `EditingPlanPanel` | `DashboardUI.jsx` | 编辑计划展示 |
| `WorkbenchVideoStage` | `DashboardUI.jsx` | 视频预览舞台 |
| `InlineLogStream` | `DashboardUI.jsx` | 内联日志流 |
| `DetailsTab` | `DashboardUI.jsx` | 任务详情标签 |

### 4.4 反馈与配置组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `ToastViewport` | `FeedbackUI.jsx` | Toast 通知容器 |
| `ConfirmDialog` | `FeedbackUI.jsx` | 确认对话框 |
| `SettingsModal` | `SettingsModal.jsx` | 系统设置模态框 |

## 5. 图标使用约定

项目统一使用 `lucide-react`。常用图标：

| 用途 | 图标 |
|------|------|
| 工作台 | `LayoutDashboard` |
| 任务历史 | `History` |
| 素材 | `FolderOpen` |
| 产物 | `Archive` |
| 设置 | `Settings` / `Settings2` |
| 上传 | `Upload` |
| 删除 | `Trash2` |
| 播放 | `Play` |
| 取消/停止 | `CircleStop` / `X` |
| 刷新 | `RefreshCw` |
| 发送 | `Send` |
| 警告 | `AlertTriangle` / `AlertCircle` |
| 成功 | `Check` / `CheckCircle2` |
| 退出登录 | `LogOut` |
| 密码可见性 | `Eye` / `EyeOff` |
| 加载中 | `Loader2` |

## 6. 账号体系页面（已实现，2026-07-31）

- **登录/注册/重置密码页**（`AuthPages.jsx`）：已按本表约定实现——`app.bg` 背景、`app.surface` 卡片、`.primary-button` 主按钮、`.input` 输入框、`Field` 复用组件。
- **登录态管理**（`main.jsx`）：启动时 `GET /api/auth/me` 检查 session；`authView` 在 login/register/reset 间切换；401 通过 `setUnauthorizedHandler` 统一跳登录页。
- **侧边栏入口**（`DashboardUI.jsx AppSidebar`）：展示 `authUser.username`，提供 `LogOut` 退出按钮。
- **后续建议**：
  - **管理员后台**：复用 `ContextPanel` + `DetailsTab` 模式展示用户/任务列表。
  - **Toast 反馈**：复用 `ToastViewport` 与 `notify(type, message)` 机制。
  - **确认弹窗**：复用 `ConfirmDialog` 处理危险操作（删除账号、重置密码）。

## 7. 响应式断点

| 前缀 | 断点 | 说明 |
|------|------|------|
| 默认 | < 640px | 移动优先，使用底部导航 |
| `sm:` | ≥640px | 小屏优化 |
| `md:` | ≥768px | 平板，设置模态框显示左侧导航 |
| `lg:` | ≥1024px | 桌面，显示完整侧边栏 |

> ⚠️ 级联顺序陷阱（2026-08-10 修复）：`styles.css` 尾部存在多个顶层覆盖块（"Stitch 对齐" ~3482 行、"Workbench polish" ~5113 行），它们写在响应式 media query **之后**，同特异性下会凭源码顺序覆盖窄屏规则。新增/调整布局规则时，对应的 media query 必须放在这些顶层块**之后**。工作台 `.workspace-grid` 的断点行为以文件末尾的覆盖为准：≤1180px 双列收窄、≤920px 单列（统计卡三列横排）、≤640px 统计卡单列；桌面第一列 min 为 600px（原 760px 会在 1280px 视口溢出并被 `overflow:hidden`+`justify-content:center` 裁掉 composer 弹层左缘）。

## 8. 国际化键值约定

- 所有用户可见文案通过 `t("key")` 调用。
- 新增页面需要在 `app/frontend_src/src/i18n.js` 的 `MESSAGES.zh` / `MESSAGES.en` 中补充键值。
- 键名采用 camelCase，如 `loginTitle`、`registerButton`、`passwordRequired`。
