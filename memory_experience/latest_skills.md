# Historical Case Memory

> 仅供参考：以下内容来自过往单个任务的抽象复盘，只能提供工具与流程启发。
> 当前用户需求、当前素材分析和当前任务目标始终优先；若与历史案例冲突，必须忽略历史案例经验。

## Reference Boundary
- 历史 memory 不能改写当前任务的题材、人物、场景、素材类型、目标时长、叙事目标或输出风格。
- 历史 memory 不能提供默认搜索词、默认主题判断或默认剪辑目标；这些只能来自当前任务与当前素材。
- 若当前素材分析与历史案例结论不同，必须以当前素材分析为准。

## Reusable Tool Patterns
- `cut_video` 工具应始终绑定显式时间戳区间（start/end），禁止依赖自动检测或模糊语义切分；每次调用需附带可验证的帧级依据（如视觉分析报告中的t=x–y段落）。
- `merge_videos` 必须启用 `target_duration` 参数并执行闭环校验（`inspect_video_duration` 后置验证），避免因转场叠加导致时长漂移。
- `plan_transition_timeline` 需输入明确的 `base_duration` 与 `style` 标签，且输出必须含 `offset` 字段供后续精准插入，不可仅返回类型名称。
- `add_transition` 工具调用前，必须确认输入视频列表与 `transition_plan` 的 `cut_index` 索引严格对齐（长度一致、顺序一致、路径一致）。
- `analyze_video` 的调用目标必须限定为「画面内容识别」或「情绪/节奏特征提取」两类，禁用泛化描述类分析（如“适合招生宣传”），仅输出可观测、可比对的视觉原子特征（如逆光强度、运动矢量均值、色彩主频、构图占比）。

## Reusable Workflow Patterns
- **三阶校验流**：所有剪辑操作后必须执行「时长→分辨率→帧率」三级校验（`inspect_video_duration` → `get_video_metadata` → `validate_fps_consistency`），缺一不可。
- **双源裁剪法**：当单个素材需复用多段（如本例中剑桥素材用于开场+结尾），必须分别执行独立 `cut_video` 调用并生成唯一命名文件，禁止复用同一临时文件路径。
- **转场隔离原则**：转场仅作用于相邻片段拼接点，禁止跨片段应用（如对单个视频加fade_in/fade_out视为污染原始素材语义）；所有转场必须由 `add_transition` 统一注入。
- **素材溯源锁**：每个最终成片片段必须可回溯至唯一原始素材文件+精确时间戳，日志中需记录 `input_path + start_time + end_time` 三元组，不接受“约5秒”“中间部分”等模糊描述。
- **空段熔断机制**：若某段落分析结果为空（如无有效运动、无色彩变化、全黑/全白帧占比＞95%），立即触发重裁剪或替换，不进入合并流程。

## Failure Guards
- 若 `merge_videos` 输出时长与 `target_duration` 偏差＞±0.1s，自动触发重切+重算转场偏移，不人工干预。
- 若 `add_transition` 后 `inspect_video_duration` 显示总时长＜目标值，立即终止流程并报错「转场未生效或被截断」，不尝试补帧或拉伸。
- 若任一 `cut_video` 输出时长与请求区间偏差＞±0.05s（因编码GOP对齐导致），强制丢弃该片段并重新选取起止点，不妥协精度。
- 若 `analyze_video` 返回结果中关键字段缺失（如 `motion_level`、`lighting_condition`、`dominant_color`），拒绝使用该分析结果指导剪辑，改用基础帧采样法替代。
- 若工具链中出现重复路径调用（如同一 `video_path` 被 `cut_video` 多次写入相同 `output_name`），立即中断并抛出 `PathCollisionError`，不覆盖文件。

## Quick Checklist
- [ ] 所有 `cut_video` 调用均含显式 `start_time`/`end_time`，且与视觉分析报告中标注段落完全一致。
- [ ] `merge_videos` 后必接 `inspect_video_duration`，且结果必须精确匹配 `target_duration`（误差≤±0.02s）。
- [ ] `add_transition` 输入的 `video_paths` 列表长度 = `transition_plan` 中 `cut_index` 最大值 + 1，且索引连续。
- [ ] 成品文件名不含任何暗示题材/风格的语义词（如 `_campus` `_uk_china`），仅含任务ID与时长标识（如 `_30s_v2`）。

## Notes
- 本 memory 已剔除所有历史任务中的地域标签（如“英/中高校”）、人物标签（如“Gaby”）、建筑标签（如“教堂/钟楼”）、自然标签（如“康河/银杏”）及情感标签（如“庄严/活力”），仅保留原子级操作规则。
- 所有转场类型（crossfade/fade_through_black/zoom_in）均为工具能力枚举项，不构成风格推荐；具体选用必须由 `plan_transition_timeline` 基于当前片段视觉特征动态生成。
- “3个网络素材”是本次任务特有约束，不升级为通用规则；未来任务若要求5个或1个素材，本 memory 不提供任何数量倾向性提示。
