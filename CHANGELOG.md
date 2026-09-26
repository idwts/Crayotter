# Changelog

## test/s1-s3-hardening(2026-09-26)

五项外科手术式修复,全部经对抗双智能体方案讨论(三轮收敛签字)+ 实现后对抗 review(两轮,10 项发现全处置)后落地。测试:`phase3_rl/tests` 81 passed + 12 subtests,仅 2 个预存失败(`test_analysis_time_parsing` ×2,干净树对照确认与本次改动无关)。

### S1-1 失败检测统一(canonical `result_indicates_failure`)
- `phase3_rl/tool_runtime.py` 新增 `result_indicates_failure(text, markers, *, strict_status=True, full_text=False)`,为 graph.py 与 phase3_rl 共享的唯一失败判定入口。
- **两档严格度(经对抗 review 登记)**:
  - phase3_rl 档(默认):JSON dict 非空 `status != "success"` 判负、JSON list 恒成功、其余回退**首行** markers(与 `parse_tool_result_text` 逐位等价)。
  - graph.py 档:`strict_status=False, full_text=True`,markers=`("出错","失败","error",'"status": "fail"')` 全 lowered 文本扫描——与旧实现逐位等价,含 `"failed"` 不被捕获的历史怪癖(测试已钉死)。
- `script/graph.py:5842` 旧全文 markers 扫描删除,改调 canonical 函数(懒导入 + ImportError sys.path 回退)。

### S1-2 移除全局禁 SSL 补丁
- 删除 `script/tools/_shared.py` 第 40 行 `ssl._create_default_https_context = ssl._create_unverified_context`。
- **行为变更登记**:以下站点由"全局不验证"恢复为默认验证 SSL(经审计为预期加固,均为正规 CDN/官方 API,证书链完整):
  - `script/tools/source_adapters/crawler.py`(通用爬虫 opener,两处)
  - `script/tools/source_adapters/youtube.py`
  - `script/tools/_shared.py:1330`(DashScope TTS 音频 `urlretrieve`)
- `script/tools/download_bilibili_video.py` 为唯一承重站点:模块级 `_BILIBILI_SSL_CONTEXT`,两处 `urlopen` 显式传入,保持原行为。

### S1-3 cut_video moviepy 分支区间校验
- `script/tools/cut_video.py`:moviepy 回退分支在裁剪前校验 `start_time < 0 or end_time <= start_time or end_time > clip.duration + 0.05` → `ValueError`(走既有 `tool_error` 路径),杜绝非法区间污染 RL 时长观测。ffmpeg 原生分支由 `cut_video_native` 自行报错,不加重复校验。

### S2-1 RL 工具调用常驻 worker(opt-in)
- `CRAYOTTER_RL_TOOL_WORKER=1` 门控,默认关(冷路径逐字节不变)。
- `phase3_rl/tool_runner.py --serve`:JSONL 长驻循环,帧携带 `request_id` 回显 + 三态 `returncode`(0 成功 / 2 工具失败 / 1 基础设施失败,reward.py 的 -0.15 语义不变),`redirect_stdout`/`redirect_stderr` 捕获工具输出入帧。
- worker 指纹 = (python_executable, resolved runtime_root, sha256(api_config canonical json)),一 worker 终身一指纹;池大小 = `_tool_process_concurrency()`(默认 2);`CRAYOTTER_RL_TOOL_WORKER_MAX_CALLS`(默认 200)到数即退休并 kill(限制内存/状态累积)。
- **闩锁重置注册表** `_LATCH_RESETTERS`:`reset_analysis_failure_circuit` + `reset_analysis_model_fallbacks`,每请求 invoke 前重置(冷路径同样经过 `_execute_request`,天然 parity);收录标准:跨请求存续且影响观测语义。
- **显式失败语义**(对抗 review 第 4/5 项修正):读取侧用 daemon pump 线程 + `queue.get(timeout=)`,全平台兑现超时;写侧传输失败 → `_WorkerDead`(请求未达工具,安全回落冷路径);请求被接受后 worker 死亡/挂起 → 显式失败 `returncode=1`,**绝不重试**(工具非幂等)。
- worker 路径同样经过 `_global_tool_process_slot()`(POSIX 跨 Ray worker 限流 parity)。

### S3-1 产物可解码性门 `_artifact_gate`
- `phase3_rl/reward.py`:ffprobe(`FFPROBE_BIN` 可覆盖)→ cv2 回退(真实 `cap.read()` 解码尝试,不信 `CAP_PROP_FRAME_COUNT`)。
- 三态判负入口:**corrupt**(ffprobe 非零退出 / rc=0 结构性无视频流或时长≤0 / cv2 无法解码)、**io_error**(OSError/TimeoutExpired/rc=0 输出不可解析,回退旧存在性语义)、**unavailable**(无 ffprobe 且无 cv2,回退旧存在性语义)。
- 门内置于 `find_final_video_path`(per-call `gate_cache` memoize + 可选 `gate_report` 出参)。**有意语义放宽(登记)**:最新 export 判 corrupt 时回退更早的有效 export;仅 corrupt 判负,io_error/unavailable 保持旧存在性语义。
- `reward_payload["artifact_gate"]` 新字段:`{state, path, rejected_corrupt}`;无候选时 `state="no_candidate"`(与 infra unavailable 区分)。
- **功能测试修正(2026-09-26 第二轮)**:仓内 `script/dep/windows/ffprobe.CMD` 实为 Python shim(`ffprobe_shim.py`),rc=0 输出裸时长(如 `1.500000`)而非 `-of json` 契约——原实现 `json.loads` 得 float 后 `.get` 直接 AttributeError 崩溃。现:rc=0 但输出非 JSON 契约(含空 stdout、非 dict、叶类型错误)→ 落入 cv2 真实解码分支拿真实判定;cv2 也不可用时才回退 io_error/unavailable 旧语义。`except` 覆盖 `(ValueError, TypeError, AttributeError)`。

### 功能验证(真实文件/真实子进程,非 mock)
`functional_checks_s1_s3.py`(仓库根,`python functional_checks_s1_s3.py` 可复跑):
- S3-1:cv2 实写 3s mp4 → `(True,"passed")`;假字节文件 → `(False,"corrupt")`;moviepy 实剪 1.5s 片段 → 经 shim fallthrough 由 cv2 判 `(True,"passed")`;corrupt 最新 export 端到端回退更早有效文件。
- S1-3:非法区间(0-999s/3s 视频)→ 真实 `tool_error("剪辑", "invalid cut range ...")`;合法区间 → status=success + 真实文件落盘。
- S2-1:`CRAYOTTER_RL_TOOL_WORKER=1` 真实 worker 两次调用 `inspect_video_duration` 全成功(rc=0,时长 3.0s 正确)、单进程复用(calls=2)、未知工具显式 rc=1 失败。
- S1-1:4 条**真实工具返回**(成功 JSON/tool_error 中文字符串)在 graph/strict 两档判定全部正确。
- S1-2:新进程内 `_shared` 导入后全局 SSL 补丁不存在;`_download_via_bilibili_api` 真实调用流两处 urlopen 均携带 scoped 未验证 context(spy 验证)。

### 测试
- `test_reward.py`:`EpisodeRewardTests.setUp` mock 门为 `(True, "passed")`(这些测试测 reward 组合语义,fixtures 为假视频字节,与改动前的存在性语义精确等价);新增 `ArtifactGateTests` ×11(ffprobe passed/结构性 corrupt/非零 corrupt/timeout io_error/不可解析 io_error/unavailable/cv2 corrupt/corrupt 回退更早 export/全 corrupt rejected/payload 字段/no_candidate)。
- `test_tool_runtime.py`:新增 `ResultIndicatesFailureTests`(四组返回串双侧一致 + 两档严格度差异 + graph 历史怪癖钉死)+ `LatchRegistryTests`(注册表内容 + 每注册重置函数被调用)。
