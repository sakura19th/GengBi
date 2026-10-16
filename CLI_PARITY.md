# CLI 完整业务接入与验收清单

目标：用户不打开桌面窗口，也能完成赓笔现有写作业务，并获得从首次配置到长篇创作的清晰引导。现有 TXT 续写命令保持兼容。界面布局、字体和窗口显示不作为命令行业务。

业务实现与本地验收已完成。以下证据均位于 `tests/test_cli.py`，覆盖真实子进程、本机 HTTP/SSE、持久化与错误路径；不是仅检查帮助或命令存在。

| 业务 | 已接入能力 | 行为测试证据 |
|---|---|---|
| 使用引导 | 无参数入门路径、分主题指南、交互向导、下一步提示、JSON 输出 | test_no_arguments_show_guide_without_creating_config；test_wizard_imports_article_and_returns_from_invalid_choice；test_wizard_requires_terminal_without_mutation |
| 模型配置 | 端点增改删、加密密钥、模型列表、连通性检测、流程模型、诊断及高级设置 | test_workspace_configuration_roundtrip；test_advanced_settings_reach_requests_and_reject_invalid_changes；test_local_no_key_extraction_and_saved_volume_defaults |
| 项目 | 新建、TXT 导入拆章、列表、档案编辑、查看、导出、删除、ZIP 备份恢复 | test_project_chapter_roundtrip_preserves_content；test_project_backup_preserves_profiles_context_and_worldbook |
| 章节 | 读取、添加/修改、重命名、拆分/合并、排序、删除 | test_project_chapter_roundtrip_preserves_content |
| 世界书 | 新建、查看、条目编辑、开关、多选、ST 导入导出、续写注入 | test_worldbook_edit_export_import；test_project_profiles_and_worldbook_switches |
| 上下文提取 | 文章/项目、范围、分批、增量、缓存、编辑与章节复制 | test_incremental_and_batched_extraction_cache_and_custom_rule；test_project_profiles_and_worldbook_switches |
| 档案提取 | 世界观、主角、指定角色、文风、自定义约束；存储编辑和注入 | test_profile_extraction_is_persisted；test_incremental_and_batched_extraction_cache_and_custom_rule |
| 文章到世界书 | 导入、提取、保存世界书、ST 导出和后续续写 | test_article_to_worldbook_and_project_continuation |
| 项目续写 | 项目/章节、世界书、档案、回溯资料、候选保存、按次破限 | test_article_to_worldbook_and_project_continuation；test_project_profiles_and_worldbook_switches；test_jailbreak_override_reaches_http_without_changing_preset |
| 审稿与重写 | 可编辑报告、按意见修订、当前章重写、写作分析和预先规划流程 | test_audit_and_revision_keep_original；test_builtin_flow_executes_each_stage_and_preserves_original |
| 候选与历史 | 列表、查看、编辑、接受/替换、删除、历史查看清理 | test_article_to_worldbook_and_project_continuation；test_regex_preview_and_manual_candidate_edit |
| 整卷生成 | 分析、大纲、审计、逐章写作验证修订、暂停编辑恢复、取消 | test_volume_pauses_resumes_and_saves_two_chapters；test_legacy_volume_pipeline_plugin_runs_full_engine；test_terminal_checkpoint_requires_valid_action |
| 预设/正则/流程 | 资源管理、作用域、启停、预览、五类 agent 执行、编辑恢复 | test_assets_presets_regex_and_flow_roundtrip；test_regex_preview_and_manual_candidate_edit；test_custom_flow_uses_named_input_and_resumes_edited_analysis；test_failed_flow_resumes_without_repeating_analysis；test_resume_rejects_changed_worldbook_without_request |

## 验证结果与交付

- Windows 源码 CLI：77 项通过。全仓：1020 通过、45 失败、15 跳过；45 个失败测试 ID 与未修改上游基线完全一致，无新增失败。
- 独立 Windows EXE：73 通过、4 跳过。4 项为源码专用的 Qt 导入拦截、键盘中断注入、向导输入注入、暂停点输入注入；对应源码检查通过。EXE 的真实业务子进程测试通过，嵌入模块包含全部 CLI 模块且不含 Qt。
- 桌面卷编排及流程端点相关回归 60 项通过；CLI 模块 Ruff 与 git diff --check 通过。
- README、CLI_GUIDE.md、FLOW_PLUGIN_GUIDE.md、agent.md、update.md 同步维护；Windows 交付包包含 EXE、中文启动向导入口、操作及流程指南、验收说明。
- 改动继续提交到原 PR #8。CI 配置覆盖 Windows/macOS/Linux；macOS 实机尚未验证，远端 Actions 结果应以 PR 页面为准，不把配置了矩阵当成跨平台实测通过。

## 明确边界

- 测试使用隔离配置和本机 HTTP/SSE 服务，不使用用户付费密钥。测试证明请求组装及业务流程，不保证模型生成质量。
- Windows EXE 仅用于 Windows；macOS/Linux 使用 Python 3.11+ 源码入口。
- 世界书支持启用、多选和条目注入，不实现酒馆的关键词概率激活机制。
- 长文按章节边界分批，单章过长需先拆章；恢复遇到文章/档案/预设/世界书/正则变化会拒绝混用旧产物。
- 预设模板变量保留原 CLI 单次调用隔离行为；原生 ZIP 包含项目变量，但不包含全局配置、API 密钥或生成历史。
