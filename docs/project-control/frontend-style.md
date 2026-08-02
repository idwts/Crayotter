# 前端风格表

> 说明：本表记录 Crayotter 前端的设计系统、组件清单与复用约定，便于后续新增页面（登录/注册/账号/管理后台）时保持风格统一。智能体阅读后应记住：**前端基于 React + Tailwind CSS，主色调为 `#155EEF`（品牌蓝），背景为 `#F4F7FA`，所有自定义样式集中在 `styles.css`；新增账号体系页面时应复用 `DashboardUI` 中定义的按钮、输入框、卡片、模态框风格。**

## 1. 技术栈

| 项目 | 说明 |
|------|------|
| 框架 | React 18（JSX） |
| 构建工具 | Vite |
| 样式 | Tailwind CSS + 自定义 `styles.css` |
| 图标 | `lucide-react` |
| 国际化 | 自研 `i18n.js`（`MESSAGES` 对象） |
| 状态 | `useState`/`useRef`/`useCallback` + `localStorage` |

## 2. Tailwind 设计 Token

见 `app/frontend_src/tailwind.config.js`：

| Token | 值 | 用途 |
|-------|-----|------|
| `app.bg` | `#F4F7FA` | 页面背景 |
| `app.surface` | `#FFFFFF` | 卡片/浮层面板 |
| `app.panel` | `#F8FAFC` | 侧边栏/次级面板 |
| `app.line` | `#D9E1EA` | 分割线/边框 |
| `app.ink` | `#101828` | 主文字 |
| `app.soft` | `#667085` | 次要文字 |
| `app.brand` | `#155EEF` | 品牌主色/主要按钮 |
| `app.success` | `#0E9384` | 成功状态 |
| `app.warning` | `#DC6803` | 警告状态 |
| `app.danger` | `#D92D20` | 危险/删除 |

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
| `.ghost-button` | 幽灵按钮 | 低调操作 |
| `.card` / `.panel` | 卡片/面板 | 内容分区 |
| `.form-field` / `.field-label` | 表单字段 | 设置模态框 |
| `.input` / `.select` | 输入框/选择框 | 表单 |
| `.toast-viewport` / `.toast-item` | Toast 通知 | 全局反馈 |
| `.dialog-layer` / `.dialog-backdrop` | 模态框层级 | 确认对话框、设置 |
| `.confirm-dialog` | 确认对话框 | 删除确认 |
| `.settings-modal` | 设置模态框 | 系统配置 |
| `.motion-enter` | 进入动画 | 弹窗 |
| `.workbench-flow` | 工作台流程容器 | 主界面 |

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
| `Field` | 表单字段组件：label + `.input` + 可选密码可见性切换（Eye/EyeOff）+ 错误提示（`text-app-danger`） |
| `cx` | 类名拼接工具（filter(Boolean).join） |
| 布局约定 | `grid min-h-screen place-items-center bg-app-bg p-4` 居中容器 + `max-w-md rounded-2xl bg-app-surface p-8 shadow-sm` 卡片 |
| 提交按钮 | `.primary-button flex w-full items-center justify-center gap-2` + `Loader2` 旋转动画（busy 态） |
| 切换链接 | `text-app-brand hover:underline` 文字按钮（登录/注册/重置互跳） |
| 品牌 | `brand-mascot.png` 16×16 居中置于标题上方 |

### 4.2 布局组件

| 组件 | 文件 | 说明 |
|------|------|------|
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
  - **账号管理**：复用 `SettingsModal` 的左右分栏布局与 `.settings-section-stage` 样式，新增改密入口（调用 `POST /api/auth/password`）。
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

## 8. 国际化键值约定

- 所有用户可见文案通过 `t("key")` 调用。
- 新增页面需要在 `app/frontend_src/src/i18n.js` 的 `MESSAGES.zh` / `MESSAGES.en` 中补充键值。
- 键名采用 camelCase，如 `loginTitle`、`registerButton`、`passwordRequired`。
