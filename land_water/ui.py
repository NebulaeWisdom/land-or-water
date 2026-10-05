"""NiceGUI workbench for the Land-or-Water benchmark.

The page DOM is built once per visitor; a single refresh timer then updates
existing elements in place. The runtime console, the configuration table and
every selected result card keep their client elements alive, so a running batch
shows live progress without a page rerun and an idle tick touches nothing.
"""

from __future__ import annotations

import asyncio
import copy
import html
import json
import re
from pathlib import Path

import plotly.io as pio
import yaml
from nicegui import core, run, ui

from land_water.config import config_text, load_config, save_config
from land_water.dataset import generate_points
from land_water.runner import RunControl, test_connection
from land_water.stats import export_results, summarize
from land_water.store import Store

ROOT = Path.cwd()
CONFIG_PATH = ROOT / "project.yaml"
PROTOCOLS = ("chat", "responses")
ENVELOPES = ("success_data", "openai")
OMITTED_TEXT = ("", "null", "none", "~")
BUSY_STATES = ("running", "stopping")
EXPORT_MEDIA = {"csv": "text/csv", "json": "application/json", "html": "text/html"}

# One batch per server process: every visitor's page polls this same control.
CONTROL = RunControl()

CONFIG_GRID = "44px minmax(150px,1.3fr) minmax(150px,1.3fr) 90px 110px 120px 190px"
CONSOLE_GRID = "minmax(140px,1.6fr) 110px 80px 90px 100px 70px 100px 120px"
PARAM_GRID = "minmax(160px,1.4fr) 120px minmax(200px,2fr) 90px"
PARAM_TYPES = ("string", "number", "boolean", "json")
PARAM_DEFAULTS = {"string": "", "number": 0, "boolean": False, "json": {}}

# Fixed plotly config: keeping it byte-identical between updates makes the
# client reuse the chart through Plotly.react instead of Plotly.newPlot.
PLOT_CONFIG = {
    "responsive": True,
    "displaylogo": False,
    "scrollZoom": False,
    "doubleClick": False,
    "modeBarButtonsToRemove": [
        "zoom2d",
        "pan2d",
        "zoomIn2d",
        "zoomOut2d",
        "autoScale2d",
        "resetScale2d",
        "lasso2d",
        "select2d",
        "toImage",
    ],
}

# Fixed cylindrical (equirectangular) world: one degree of longitude equals one
# degree of latitude in pixels, so the plotting area is exactly 2:1.
MAP_MARGINS = {"l": 60, "r": 150, "t": 46, "b": 56}
MAP_HEIGHT = 520
MAP_X_TICKS = [-180, -90, 0, 90, 180]
MAP_Y_TICKS = [-90, -45, 0, 45, 90]
MAP_CATEGORIES = ("unrun", "land", "water", "invalid", "failed")
HTML_PAGE = """<!DOCTYPE html>
<html lang="{lang}">
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: system-ui, sans-serif; margin: 24px;">
<h1>{title}</h1>
<h2>{summary_heading}</h2>
<pre>{summary}</pre>
{chart}
</body>
</html>
"""
# Explicit popup dismissal: outside mousedown / Escape close the open select via
# hidePopup on the server (selection closes it too, in _bind_dismiss).
DISMISS_JS = (
    "() => {"
    " const root = document.querySelector('.lw-select-%(token)s');"
    " const cleanup = () => {"
    "  document.removeEventListener('mousedown', onDown, true);"
    "  document.removeEventListener('keydown', onKey, true);"
    "  if (window.__lwSelectDismiss === cleanup) delete window.__lwSelectDismiss;"
    " };"
    " const hide = () => { cleanup(); emit('lw-hide'); };"
    " const onDown = (ev) => {"
    "  const t = ev.target;"
    "  const inside = root && (root.contains(t) || (t.closest && t.closest('.q-menu, .q-dialog')));"
    "  if (!inside) hide();"
    " };"
    " const onKey = (ev) => { if (ev.key === 'Escape') hide(); };"
    " if (window.__lwSelectDismiss) window.__lwSelectDismiss();"
    " document.addEventListener('mousedown', onDown, true);"
    " document.addEventListener('keydown', onKey, true);"
    " window.__lwSelectDismiss = cleanup;"
    "}"
)

# A collapsed-on-outside-click/Escape expansion: our listeners click Quasar's own
# header toggle so the collapse stays in sync with NiceGUI's model value.
EXPAND_JS = (
    "(() => {"
    " const root = document.querySelector('.lw-expand-%(token)s');"
    " const cleanup = () => {"
    "  document.removeEventListener('mousedown', onDown, true);"
    "  document.removeEventListener('keydown', onKey, true);"
    "  if (window.__lwExpandDismiss === cleanup) delete window.__lwExpandDismiss;"
    " };"
    " const collapse = () => {"
    "  cleanup();"
    "  const icon = root && root.querySelector('.q-expansion-item__toggle-icon');"
    "  if (icon) icon.click();"
    " };"
    " const onDown = (ev) => {"
    "  const t = ev.target;"
    "  const inside = root && (root.contains(t) || (t.closest && t.closest('.q-menu, .q-dialog')));"
    "  if (!inside) collapse();"
    " };"
    " const onKey = (ev) => { if (ev.key === 'Escape' && !document.querySelector('.q-menu')) collapse(); };"
    " if (window.__lwExpandDismiss) window.__lwExpandDismiss();"
    " document.addEventListener('mousedown', onDown, true);"
    " document.addEventListener('keydown', onKey, true);"
    " window.__lwExpandDismiss = cleanup;"
    "})();"
)
EXPAND_CLEANUP_JS = "(() => { if (window.__lwExpandDismiss) window.__lwExpandDismiss(); })();"

# UI language: root ui.language selects the default, ui.languages the switchable set.
LOCALES = {"zh": "zh-CN", "en": "en-US"}
STRINGS = {
    "zh": {
        "subtitle": "批量评测工作台 · 网格点陆水判定",
        "language": "语言",
        "tab_config": "配置与运行",
        "tab_results": "结果与地图",
        "state_idle": "空闲",
        "state_running": "运行中",
        "state_stopping": "正在停止",
        "state_stopped": "已停止",
        "state_completed": "已完成",
        "state_failed": "失败",
        "batch_none": "批次：无",
        "batch_names": "批次：{names}",
        "elapsed": "已用时间 {seconds} s",
        "start_run": "开始运行（已选 {count} 个）",
        "stop": "停止",
        "total_none": "合计 —",
        "total_preparing": "合计 准备中",
        "total_progress": "合计 {completed}/{total} · {percent}",
        "rate_none": "req/s —",
        "rate_wait": "req/s 等待首个结果",
        "rate_value": "req/s {rate}",
        "rate_zero": "req/s 0",
        "eta_value": "ETA {eta}",
        "eta_none": "—",
        "eta_zero": "0 s",
        "eta_stopped": "已停止",
        "eta_wait": "等待首个结果",
        "failures": "失败 {count}",
        "cached_skipped": "缓存/已跳过 {count}",
        "tokens_total": "累计 token {value}",
        "run_error": "运行错误：{error}",
        "col_test": "测试",
        "col_progress": "完成/总数",
        "col_percent": "百分比",
        "col_rate": "req/s",
        "col_eta": "ETA",
        "col_failures": "失败",
        "col_latency": "平均延迟 s",
        "col_tokens": "累计 token",
        "console_empty": "尚未运行任何批次：在「配置与运行」勾选配置后点击「开始运行」。",
        "preparing": "准备中",
        "config_title": "配置与运行",
        "add_config": "新增配置",
        "config_hint": "勾选要运行或断点续传的配置（可多选），点上方「开始运行」。运行中每点结果即时落库，已完成的点自动跳过。",
        "col_pick": "选",
        "col_name": "名称",
        "col_model": "模型",
        "col_protocol": "协议",
        "col_plan": "计划点数",
        "col_cached": "已缓存完成",
        "col_actions": "操作",
        "edit": "编辑",
        "ping": "连通性",
        "config_empty": "暂无配置：用「新增配置」从 ui.new_test 模板创建，或导入完整项目配置。",
        "editor_none": "未选择配置。点击列表中某一行的「编辑」载入配置，或用「新增配置」创建。",
        "editing": "正在编辑：{name}{suffix}",
        "editing_new_suffix": "（新配置 · 尚未保存）",
        "unnamed": "（未命名）",
        "collapse": "收起",
        "advanced": "高级参数（并发 / 限速 / 重试 / 超时 / 采样 / 提示词）",
        "f_envelope": "response_envelope（仅 chat 生效）",
        "f_tpm": "limits.tpm（留空 = null）",
        "f_max_output": "max_output_tokens（留空 = null）",
        "f_temperature": "temperature（留空 = null）",
        "f_proxy": "proxy（留空 = null）",
        "f_point_count": "point_count（留空 = 全网格）",
        "f_prompt": "prompt（支持 {longitude} / {latitude}）",
        "params_title": "附加请求参数 request_parameters（key 支持点号嵌套；标准字段 model / messages / input / stream / max_tokens / max_output_tokens / temperature 优先，不会被覆盖）",
        "params_hint": "提示：chat 填平铺 reasoning_effort，responses 填嵌套 reasoning.effort；string 值 low / medium / high。NONE 表示不发送该参数（不代表不思考，网关默认 high）。reasoning_tokens 不保证随 effort 单调变化。",
        "params_empty": "无附加参数。",
        "params_add": "添加参数",
        "params_key": "key",
        "params_type": "类型",
        "params_value": "值",
        "params_remove": "删除",
        "type_string": "字符串",
        "type_number": "数字",
        "type_boolean": "布尔",
        "type_json": "JSON",
        "save_config": "保存配置",
        "cancel": "取消",
        "copy_config": "复制为新配置",
        "delete_config": "删除此配置",
        "guard_rename_running": "该测试正在运行中，改名会让后台继续按旧名字写入；请先点击停止，再改名",
        "guard_delete_result_running": "该测试正在运行中，删除后后台仍会继续按该名字写入；请先点击停止，再删除",
        "guard_delete_config_running": "该测试正在运行中，删除配置会让后台继续按旧名字写入；请先点击停止，再删除",
        "saved": "已保存「{name}」到 project.yaml。",
        "err_format": "字段格式错误：{error}",
        "err_name_required": "保存失败：name 为必填项。",
        "config_missing": "配置「{name}」已不存在",
        "err_name_exists": "保存失败：名称「{name}」已存在。",
        "err_result_exists": "保存失败：结果库里已有「{name}」，请换一个名字。",
        "discarded": "已放弃未保存的改动。",
        "new_draft": "已按 ui.new_test 生成新配置草稿；修改后点「保存配置」写入 project.yaml。",
        "copied": "已生成副本草稿「{name}」；修改后点「保存配置」。",
        "rename_config_title": "重命名配置「{old}」→「{target}」",
        "rename_sync": "同时重命名结果",
        "rename_will": "将同时重命名 {count} 条结果",
        "rename_stay": "{count} 条结果会留在旧名字「{old}」下",
        "confirm_save": "确认保存",
        "notify_renamed": "已将 {rows} 条结果从「{old}」重命名为「{target}」",
        "notify_saved_stay": "已保存「{target}」；{count} 条结果留在「{old}」下",
        "delete_config_title": "删除配置「{name}」？",
        "delete_config_body": "仅从 project.yaml 的 tests 列表移除该配置；数据库 results.sqlite3 的历史结果完整保留，可在「结果与地图」中继续查看（标记为「历史 · 配置已删除」）。",
        "confirm_delete": "确认删除",
        "deleted_config": "已删除配置「{name}」；历史结果仍在数据库中。",
        "ping_title": "连通性测试：{name}",
        "ping_hint": "使用已保存的配置实发一条请求；未保存的草稿改动不生效。结果不入库。",
        "ping_status": "测试中…",
        "ping_ok": "可用 · 延迟 {latency}s · point ({lon}, {lat})",
        "ping_bad": "不可用 · {error}",
        "ping_answer": "answer：{answer}",
        "raw_request": "原始 request_json",
        "raw_response": "原始 response_json（含 usage）",
        "no_body": "（无响应体）",
        "close": "关闭",
        "io_title": "导入 / 导出（YAML · JSON）",
        "io_upload": "导入 YAML / JSON",
        "export_yaml": "导出 YAML",
        "export_json": "导出 JSON",
        "io_hint": "导入会用文件内容整体替换 paths / ui / tests 并立即写入 project.yaml；数据库 results.sqlite3 不受影响。",
        "import_parse_error": "导入失败：YAML 解析错误：{error}",
        "import_schema_error": "导入失败：需要包含 paths / ui / tests 的完整项目配置。",
        "import_ok": "已导入：{count} 个配置（paths / ui 一并替换，已写入 project.yaml；results.sqlite3 未改动）。",
        "root_title": "根配置 paths / ui（高级）",
        "root_apply": "应用根配置",
        "root_hint": "编辑 paths / ui（含 ui.new_test 模板）后点应用，写回 project.yaml。",
        "root_error": "解析失败：{error}",
        "root_saved": "根配置已保存并生效（refresh_seconds 同步到刷新定时器）。",
        "results_title": "结果与地图",
        "results_hint": "结果集来自 project.yaml 与数据库；已删除配置的历史结果带「历史 · 配置已删除」标记。多选后可并排对比。空闲时图表不重建，只有数据库版本变化才刷新。",
        "results_label": "结果集（可多选对比）",
        "archived": "{name}　（历史 · 配置已删除）",
        "card_rename": "改名",
        "card_delete": "删除结果",
        "version": "数据版本 {count} 行 · 最新 {latest} · 刷新 {refreshes} 次 · 格宽 {step}°{note}",
        "version_note_history": " · 历史格宽",
        "rename_result_title": "重命名结果集「{name}」",
        "rename_result_body": "当前 {count} 条结果；确认后整集迁移到新名字。此操作独立于配置管理。",
        "rename_result_new": "新名字",
        "rename_result_confirm": "确认改名",
        "err_new_name_required": "新名字不能为空",
        "err_target_taken": "「{name}」已存在（配置或结果），请换一个名字",
        "notify_result_renamed": "已将 {rows} 条结果从「{old}」重命名为「{new}」",
        "delete_result_title": "删除结果集「{name}」？",
        "delete_result_body": "将删除数据库里的全部 {count} 条结果，不可撤销。",
        "notify_result_deleted": "已删除「{name}」的 {rows} 条结果（不可撤销）",
        "stats_total": "总数 {value}",
        "stats_valid": "有效 {value}",
        "stats_invalid": "无效 {value}",
        "stats_failed": "失败 {value}",
        "stats_correct": "正确 {value}",
        "stats_accuracy": "正确率 {value}",
        "stats_tokens": "tokens  input {input} · output {output} · total {total} · reasoning {reasoning} · cached {cached}",
        "stats_latency": "延迟  平均 {mean} s · P50 {p50} s · P95 {p95} s · 输出 TPS（Σoutput_tokens / Σlatency） {tps}",
        "stats_usage": "usage 报告数 {count}",
        "map_caption": "格心填色：浅灰=未跑 / land 绿 / water 蓝 / invalid 橙 / failed 红；固定圆柱投影，悬停显示经度、纬度、truth 与 prediction。",
        "export_png": "导出 PNG",
        "export_kind": "导出 {kind}",
        "map_axis_x": "经度 Longitude",
        "map_axis_y": "纬度 Latitude",
        "map_colorbar": "状态",
        "map_title": "{name} · {step}° 格心陆/水填色图{note}",
        "map_history_note": "（历史配置已删除，格宽 = ui.map.history_step_degrees）",
        "cat_unrun": "未跑",
        "map_hover": "经度 %{x}° · 纬度 %{y}°<br>状态 %{customdata[0]}<br>truth %{customdata[1]}<br>prediction %{customdata[2]}<extra></extra>",
        "summary_heading": "统计",
        "chart_title_suffix": "格心陆/水填色图",
    },
    "en": {
        "subtitle": "Batch workbench · land or water over the world grid",
        "language": "Language",
        "tab_config": "Config & Run",
        "tab_results": "Results & Map",
        "state_idle": "Idle",
        "state_running": "Running",
        "state_stopping": "Stopping",
        "state_stopped": "Stopped",
        "state_completed": "Completed",
        "state_failed": "Failed",
        "batch_none": "Batch: none",
        "batch_names": "Batch: {names}",
        "elapsed": "Elapsed {seconds} s",
        "start_run": "Start run ({count} selected)",
        "stop": "Stop",
        "total_none": "Total —",
        "total_preparing": "Total preparing",
        "total_progress": "Total {completed}/{total} · {percent}",
        "rate_none": "req/s —",
        "rate_wait": "req/s waiting for first result",
        "rate_value": "req/s {rate}",
        "rate_zero": "req/s 0",
        "eta_value": "ETA {eta}",
        "eta_none": "—",
        "eta_zero": "0 s",
        "eta_stopped": "stopped",
        "eta_wait": "waiting for first result",
        "failures": "Failures {count}",
        "cached_skipped": "Cached/skipped {count}",
        "tokens_total": "Cumulative tokens {value}",
        "run_error": "Run error: {error}",
        "col_test": "Test",
        "col_progress": "Done/Total",
        "col_percent": "Percent",
        "col_rate": "req/s",
        "col_eta": "ETA",
        "col_failures": "Failed",
        "col_latency": "Mean latency s",
        "col_tokens": "Total tokens",
        "console_empty": "No batch yet: tick configs under Config & Run, then click Start run.",
        "preparing": "preparing",
        "config_title": "Config & Run",
        "add_config": "New config",
        "config_hint": "Tick the configs to run or resume (multi-select), then click Start run above. Every point is stored immediately and finished points are skipped.",
        "col_pick": "Pick",
        "col_name": "Name",
        "col_model": "Model",
        "col_protocol": "Protocol",
        "col_plan": "Planned points",
        "col_cached": "Cached done",
        "col_actions": "Actions",
        "edit": "Edit",
        "ping": "Connectivity",
        "config_empty": "No config: use New config (ui.new_test template) or import a full project config.",
        "editor_none": "No config selected. Click Edit on a row, or use New config.",
        "editing": "Editing: {name}{suffix}",
        "editing_new_suffix": " (new · unsaved)",
        "unnamed": "(unnamed)",
        "collapse": "Collapse",
        "advanced": "Advanced (concurrency / limits / retries / timeout / sampling / prompt)",
        "f_envelope": "response_envelope (chat only)",
        "f_tpm": "limits.tpm (blank = null)",
        "f_max_output": "max_output_tokens (blank = null)",
        "f_temperature": "temperature (blank = null)",
        "f_proxy": "proxy (blank = null)",
        "f_point_count": "point_count (blank = full grid)",
        "f_prompt": "prompt (supports {longitude} / {latitude})",
        "params_title": "Extra request parameters (dotted keys nest; standard fields model / messages / input / stream / max_tokens / max_output_tokens / temperature take precedence and are never overridden)",
        "params_hint": "Hint: for chat use the flat key reasoning_effort; for responses use the nested key reasoning.effort; string values low / medium / high. NONE means the parameter is not sent (not the same as no thinking; the gateway default is high). reasoning_tokens is not guaranteed to vary monotonically with effort.",
        "params_empty": "No extra parameters.",
        "params_add": "Add parameter",
        "params_key": "key",
        "params_type": "Type",
        "params_value": "Value",
        "params_remove": "Remove",
        "type_string": "string",
        "type_number": "number",
        "type_boolean": "boolean",
        "type_json": "JSON",
        "save_config": "Save config",
        "cancel": "Cancel",
        "copy_config": "Copy as new",
        "delete_config": "Delete this config",
        "guard_rename_running": "This test is running: renaming would leave the background run writing under the old name; stop it first, then rename",
        "guard_delete_result_running": "This test is running: deleting would still leave the background run writing under this name; stop it first, then delete",
        "guard_delete_config_running": "This test is running: deleting the config would leave the background run writing under the old name; stop it first, then delete",
        "saved": "Saved {name} to project.yaml.",
        "err_format": "Invalid field: {error}",
        "err_name_required": "Save failed: name is required.",
        "config_missing": "Config {name} no longer exists",
        "err_name_exists": "Save failed: {name} already exists.",
        "err_result_exists": "Save failed: results already exist for {name}; choose another name.",
        "discarded": "Discarded unsaved changes.",
        "new_draft": "Created a draft from ui.new_test; click Save config to write it to project.yaml.",
        "copied": "Created copy draft {name}; click Save config after editing.",
        "rename_config_title": "Rename config {old} → {target}",
        "rename_sync": "Rename results too",
        "rename_will": "Will rename {count} results too",
        "rename_stay": "{count} results will stay under the old name {old}",
        "confirm_save": "Confirm save",
        "notify_renamed": "Renamed {rows} results from {old} to {target}",
        "notify_saved_stay": "Saved {target}; {count} results stay under {old}",
        "delete_config_title": "Delete config {name}?",
        "delete_config_body": "Only removes this config from project.yaml; historical results in results.sqlite3 are kept and stay visible under Results & Map (labelled history · config deleted).",
        "confirm_delete": "Delete",
        "deleted_config": "Deleted config {name}; historical results remain in the database.",
        "ping_title": "Connectivity test: {name}",
        "ping_hint": "Sends one real request with the saved config; unsaved draft changes do not apply. Nothing is stored.",
        "ping_status": "Testing…",
        "ping_ok": "Usable · latency {latency}s · point ({lon}, {lat})",
        "ping_bad": "Unusable · {error}",
        "ping_answer": "answer: {answer}",
        "raw_request": "Raw request_json",
        "raw_response": "Raw response_json (incl. usage)",
        "no_body": "(no response body)",
        "close": "Close",
        "io_title": "Import / Export (YAML · JSON)",
        "io_upload": "Import YAML / JSON",
        "export_yaml": "Export YAML",
        "export_json": "Export JSON",
        "io_hint": "Import replaces paths / ui / tests with the file content and writes project.yaml immediately; results.sqlite3 is untouched.",
        "import_parse_error": "Import failed: YAML parse error: {error}",
        "import_schema_error": "Import failed: a full project config with paths / ui / tests is required.",
        "import_ok": "Imported {count} configs (paths / ui replaced, written to project.yaml; results.sqlite3 unchanged).",
        "root_title": "Root paths / ui (advanced)",
        "root_apply": "Apply root config",
        "root_hint": "Edit paths / ui (incl. the ui.new_test template), then apply to write project.yaml.",
        "root_error": "Parse failed: {error}",
        "root_saved": "Root config saved and applied (refresh_seconds synced to the refresh timer).",
        "results_title": "Results & Map",
        "results_hint": "Result sets come from project.yaml and the database; history of deleted configs is labelled history · config deleted. Multi-select to compare. While idle the charts are never rebuilt; they refresh only when the database version changes.",
        "results_label": "Result sets (multi-select to compare)",
        "archived": "{name}　(history · config deleted)",
        "card_rename": "Rename",
        "card_delete": "Delete results",
        "version": "Data version {count} rows · latest {latest} · refreshes {refreshes} · cell {step}°{note}",
        "version_note_history": " · history cell size",
        "rename_result_title": "Rename result set {name}",
        "rename_result_body": "Currently {count} results; confirming migrates the whole set. This is independent of config management.",
        "rename_result_new": "New name",
        "rename_result_confirm": "Rename",
        "err_new_name_required": "New name is required",
        "err_target_taken": "{name} already exists (config or results); choose another name",
        "notify_result_renamed": "Renamed {rows} results from {old} to {new}",
        "delete_result_title": "Delete result set {name}?",
        "delete_result_body": "Deletes all {count} results from the database. This cannot be undone.",
        "notify_result_deleted": "Deleted {rows} results of {name} (cannot be undone)",
        "stats_total": "Total {value}",
        "stats_valid": "Valid {value}",
        "stats_invalid": "Invalid {value}",
        "stats_failed": "Failed {value}",
        "stats_correct": "Correct {value}",
        "stats_accuracy": "Accuracy {value}",
        "stats_tokens": "tokens  input {input} · output {output} · total {total} · reasoning {reasoning} · cached {cached}",
        "stats_latency": "Latency  mean {mean} s · P50 {p50} s · P95 {p95} s · output TPS (Σoutput_tokens / Σlatency) {tps}",
        "stats_usage": "usage reports {count}",
        "map_caption": "Filled cells: pale grey = not run / land green / water blue / invalid orange / failed red; fixed equirectangular projection, hover shows longitude, latitude, truth and prediction.",
        "export_png": "Export PNG",
        "export_kind": "Export {kind}",
        "map_axis_x": "Longitude",
        "map_axis_y": "Latitude",
        "map_colorbar": "Status",
        "map_title": "{name} · {step}° filled-cell map{note}",
        "map_history_note": " (deleted config, cell size = ui.map.history_step_degrees)",
        "cat_unrun": "unrun",
        "map_hover": "Longitude %{x}° · Latitude %{y}°<br>status %{customdata[0]}<br>truth %{customdata[1]}<br>prediction %{customdata[2]}<extra></extra>",
        "summary_heading": "Summary",
        "chart_title_suffix": "filled-cell map",
    },
}


def translate(lang: str, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["zh"]
    text = table.get(key, STRINGS["zh"].get(key, key))
    return text.format(**kwargs) if kwargs else text


STYLES = """
body { background: #f2f4f2; }
.lw-body { max-width: 1900px; margin: 0 auto; padding: 14px 20px 64px; }
.lw-title { font-size: 24px; font-weight: 700; color: #12283f; letter-spacing: .5px; }
.lw-subtitle { color: #5a6b78; font-size: 13px; }
.lw-h2 { font-size: 17px; font-weight: 600; color: #12283f; }
.lw-h3 { font-size: 15px; font-weight: 600; color: #0d7d78; }
.lw-sub { font-size: 12px; color: #6b7a86; }
.lw-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.lw-console { position: sticky; top: 0; z-index: 60; background: #ffffff;
  border: 1px solid #d9dfdb; border-left: 6px solid #0d7d78; border-radius: 10px;
  padding: 10px 14px; box-shadow: 0 3px 12px rgba(18,40,63,.10); }
.lw-state { font-size: 16px; font-weight: 700; color: #12283f; min-width: 96px;
  text-align: center; background: #e7f2f0; border-radius: 6px; padding: 4px 10px; }
.lw-progress { width: 220px; }
.lw-error { color: #b3261e; font-size: 12px; white-space: pre-wrap; }
.lw-panel { border: 1px solid #dde3df; border-radius: 10px; background: #fff;
  padding: 14px 16px; width: 100%; box-shadow: none; }
.lw-card { border: 1px solid #dde3df; border-radius: 10px; width: 100%;
  padding: 12px 14px; box-shadow: 0 1px 4px rgba(18,40,63,.06); }
.lw-row { border-bottom: 1px solid #eef1ef; padding: 4px 0; align-items: center; }
.lw-head { color: #5a6b78; font-size: 12px; font-weight: 600; border-bottom: 1px solid #cfd8d3; }
.lw-name { font-weight: 600; color: #12283f; }
.lw-stats { white-space: pre-wrap; line-height: 1.55; color: #223645; }
.lw-raw { max-height: 300px; overflow: auto; background: #0f1c27; padding: 10px 12px; border-radius: 8px; }
.lw-raw .lw-mono { color: #dbe6ef; white-space: pre-wrap; word-break: break-all; }
.lw-ok { color: #0b7a3b; font-weight: 600; }
.lw-bad { color: #b3261e; font-weight: 600; }
.lw-warn { color: #b3261e; font-size: 12px; }
.lw-ping-status { font-size: 14px; }
.lw-tabbar button { text-transform: none; }
@media (max-width: 1200px) { .lw-3col { grid-template-columns: repeat(2, minmax(0,1fr)) !important; } }
@media (max-width: 760px) { .lw-3col, .lw-2col { grid-template-columns: 1fr !important; } }
"""


def _safe(identity: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "-", identity).strip("-") or "item"


def _as_text(value) -> str:
    return "" if value is None else str(value)


def _optional_int(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in OMITTED_TEXT else int(text)


def _optional_float(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in OMITTED_TEXT else float(text)


def _optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value) -> int:
    return int(float(value))


def _number(value) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _fmt_int(value) -> str:
    return "—" if value is None else f"{value:,}"


def _fmt_float(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _discrete_colorscale(colors) -> list:
    """Five flat bands for z = 0..4 (unrun / land / water / invalid / failed)."""
    scale = []
    for index, category in enumerate(MAP_CATEGORIES):
        low = index / len(MAP_CATEGORIES)
        high = (index + 1) / len(MAP_CATEGORIES)
        scale.append([low, colors[category]])
        scale.append([high, colors[category]])
    return scale


def map_labels(lang: str) -> dict:
    """Localized axis/colorbar/hover labels for the heatmap figure."""
    return {
        "axis_x": translate(lang, "map_axis_x"),
        "axis_y": translate(lang, "map_axis_y"),
        "colorbar": translate(lang, "map_colorbar"),
        "categories": [translate(lang, "cat_unrun"), "land", "water", "invalid", "failed"],
        "hover": translate(lang, "map_hover"),
    }


def _heatmap_figure(rows, step, colors, title: str, uirevision: str, labels: dict) -> dict:
    """Filled-cell heatmap of the regular lat/lon lattice: no scatter markers."""
    lon_count = int(360 / step)
    lat_count = int(180 / step)
    x0 = -180 + step / 2
    y0 = -90 + step / 2
    z = [[0] * lon_count for _ in range(lat_count)]
    custom = [[[labels["categories"][0], "—", "—"] for _ in range(lon_count)] for _ in range(lat_count)]
    for row in rows:
        status = row["status"]
        if status == "ok":
            value = 1 if (row["answer"] or "").strip().lower() == "land" else 2
        elif status == "invalid":
            value = 3
        else:
            value = 4
        column = int(round((row["longitude"] - x0) / step))
        line = int(round((row["latitude"] - y0) / step))
        z[line][column] = value
        custom[line][column] = [
            labels["categories"][value],
            _as_text(row["truth"]) or "—",
            _as_text(row["answer"]) or "—",
        ]
    return {
        "data": [
            {
                "type": "heatmap",
                "z": z,
                "x0": x0,
                "y0": y0,
                "dx": step,
                "dy": step,
                "xgap": 0,
                "ygap": 0,
                "zsmooth": False,
                "zmin": -0.5,
                "zmax": 4.5,
                "colorscale": _discrete_colorscale(colors),
                "customdata": custom,
                "hovertemplate": labels["hover"],
                "colorbar": {
                    "title": {"text": labels["colorbar"]},
                    "tickmode": "array",
                    "tickvals": [0, 1, 2, 3, 4],
                    "ticktext": list(labels["categories"]),
                    "thickness": 18,
                    "outlinewidth": 0,
                },
            }
        ],
        "layout": {
            "title": {"text": title, "x": 0.5, "xanchor": "center"},
            "xaxis": {
                "title": {"text": labels["axis_x"]},
                "range": [-180, 180],
                "tickvals": MAP_X_TICKS,
                "fixedrange": True,
                "constrain": "domain",
                "zeroline": False,
            },
            "yaxis": {
                "title": {"text": labels["axis_y"]},
                "range": [-90, 90],
                "tickvals": MAP_Y_TICKS,
                "fixedrange": True,
                "constrain": "domain",
                "scaleanchor": "x",
                "scaleratio": 1,
                "zeroline": False,
            },
            "dragmode": False,
            "autosize": True,
            "height": MAP_HEIGHT,
            "margin": dict(MAP_MARGINS),
            "plot_bgcolor": "#ffffff",
            "showlegend": False,
            "uirevision": uirevision,
        },
        "config": PLOT_CONFIG,
    }


def _png_height(width: int) -> int:
    """Image height that keeps the exported plotting area at the fixed 2:1 ratio."""
    return int(round((width - MAP_MARGINS["l"] - MAP_MARGINS["r"]) / 2 + MAP_MARGINS["t"] + MAP_MARGINS["b"]))


def _write_heatmap_html(database: Path, directory: Path, name: str, step, colors, title: str, lang: str) -> Path:
    store = Store(database)
    try:
        rows = store.view_rows(name)
    finally:
        store.close()
    figure = _heatmap_figure(rows, step, colors, title, name, map_labels(lang))
    chart = pio.to_html(
        {"data": figure["data"], "layout": figure["layout"]},
        config=figure["config"],
        full_html=False,
        include_plotlyjs=True,
        validate=False,
    )
    page = HTML_PAGE.format(
        lang=lang,
        title=html.escape(title),
        summary_heading=html.escape(translate(lang, "summary_heading")),
        summary=html.escape(json.dumps(summarize(rows), ensure_ascii=False, indent=2)),
        chart=chart,
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.html"
    path.write_text(page, encoding="utf-8")
    return path


def _load_tick(database: Path, console_names, started_at, stamp_names, config_names) -> dict:
    """One Store per tick: progress, per-batch progress, result stamps and names."""
    store = Store(database)
    try:
        unique = list(dict.fromkeys([*console_names, *config_names]))
        progress = {name: store.progress(name) for name in unique}
        batch = (
            {name: store.progress(name, since=started_at) for name in console_names}
            if started_at
            else {}
        )
        stamps = {name: store.result_stamp(name) for name in stamp_names}
        names = store.test_names()
    finally:
        store.close()
    return {"progress": progress, "batch": batch, "stamps": stamps, "names": names}


def _load_results(database: Path, names) -> dict:
    """Rows and change marker read in one read transaction, so they agree."""
    store = Store(database)
    try:
        store.connection.isolation_level = None
        payload = {}
        for name in names:
            store.connection.execute("BEGIN")
            try:
                stamp = store.result_stamp(name)
                rows = store.view_rows(name)
            finally:
                store.connection.execute("COMMIT")
            payload[name] = {"rows": rows, "stamp": stamp}
        return payload
    finally:
        store.close()


def _write_export(database: Path, directory: Path, name: str) -> dict:
    store = Store(database)
    try:
        rows = store.results(name)
    finally:
        store.close()
    return export_results(rows, directory, name)


def _probe_name(database: Path, name: str) -> tuple:
    """Fresh row count and the current result-set names for the dialogs."""
    store = Store(database)
    try:
        return store.result_stamp(name)[0], store.test_names()
    finally:
        store.close()


def _rename_results(database: Path, old_name: str, new_name: str) -> int:
    store = Store(database)
    try:
        return store.rename_results(old_name, new_name)
    finally:
        store.close()


def _delete_results(database: Path, name: str) -> int:
    store = Store(database)
    try:
        return store.delete_results(name)
    finally:
        store.close()


class Panel:
    """Per-client page state: one DOM construction plus in-place tick updates."""

    def __init__(self) -> None:
        self.project: dict = {}
        self.lang = "zh"
        self.languages = ("zh", "en")
        self.database = ROOT / "data" / "results.sqlite3"
        self.exports_dir = ROOT / "exports"
        self.refresh_seconds = 2
        self.map_config: dict = {}
        self.timer = None
        self.plan_counts: dict[str, int] = {}
        self.config_counts: dict[str, int] = {}
        self.store_names: list[str] = []
        self.result_names: tuple = ()
        self.initial_name: str | None = None
        self.initial_payload: dict | None = None
        self.run_selection: set[str] = set()
        self.selected_results: list[str] = []
        self._desired_results: list[str] = []
        self._pending_options = None
        self.cards_lock = asyncio.Lock()
        self.tick_seq = 0
        self.painted_seq = 0
        self.editing_name: str | None = None
        self.draft: dict | None = None
        self.draft_is_new = False
        self.editor_widgets: dict = {}
        self.editor_heading = None
        self.editor_feedback = None
        self.editor_name_input = None
        self.editor_delete_button = None
        self.editor_name_guard = None
        self.editor_delete_guard = None
        self.editor_guard_blocked = None
        self.cards_guard_state = None
        self.params_container = None
        self.param_rows: list = []
        self.expansions: list = []
        self.config_rows: dict[str, dict] = {}
        self.console_names: tuple = ()
        self.console_test_rows: dict[str, dict] = {}
        self.cards: dict[str, dict] = {}
        self.stamps: dict[str, tuple] = {}
        self.tab_buttons: dict = {}
        self.panels: dict = {}

    # ------------------------------------------------------------------ build

    def build(self) -> None:
        self._reload()
        self.plan_counts = {test["name"]: len(generate_points(test)) for test in self.project["tests"]}
        store = Store(self.database)
        try:
            self.config_counts = {
                test["name"]: store.progress(test["name"])["count"] for test in self.project["tests"]
            }
            self.store_names = store.test_names()
            self.result_names = tuple(self.store_names)
            self.initial_name = self._pick_initial_name()
        finally:
            store.close()
        self.selected_results = [self.initial_name] if self.initial_name else []
        self._desired_results = list(self.selected_results)
        if self.initial_name is not None:
            self.initial_payload = _load_results(self.database, (self.initial_name,))[self.initial_name]

        ui.add_head_html(f"<style>{STYLES}</style>")
        with ui.column().classes("lw-body w-full gap-3"):
            self._build_header()
            self._build_console()
            with ui.row().classes("lw-tabbar gap-2"):
                self.tab_buttons["config"] = ui.button(
                    self.t("tab_config"), on_click=lambda: self._select_tab("config")
                )
                self.tab_buttons["results"] = ui.button(
                    self.t("tab_results"), on_click=lambda: self._select_tab("results")
                )
            self.panels["config"] = ui.column().classes("w-full gap-4").props("id=config-panel")
            self.panels["results"] = ui.column().classes("w-full gap-4").props("id=results-panel")
            with self.panels["config"]:
                self._build_config_panel()
            with self.panels["results"]:
                self._build_results_panel()
        self._select_tab("config")
        if self.initial_name is not None:
            self._create_card(self.initial_name, self.initial_payload)
        self._sync_start_button()
        self.timer = ui.timer(self.refresh_seconds, self._tick)

    def _reload(self) -> None:
        self.project = load_config(CONFIG_PATH)
        self.lang = self.project["ui"]["language"]
        self.languages = tuple(self.project["ui"]["languages"])
        self.database = ROOT / self.project["paths"]["database"]
        self.exports_dir = ROOT / self.project["paths"]["exports"]
        self.refresh_seconds = self.project["ui"]["refresh_seconds"]
        self.map_config = self.project["ui"]["map"]

    def t(self, key: str, **kwargs) -> str:
        return translate(self.lang, key, **kwargs)

    def _commit(self, project: dict) -> None:
        """Persist a mutated project and refresh every dependent part of the page."""
        save_config(CONFIG_PATH, project)
        self.project = project
        self.database = ROOT / project["paths"]["database"]
        self.exports_dir = ROOT / project["paths"]["exports"]
        self.refresh_seconds = project["ui"]["refresh_seconds"]
        self.map_config = project["ui"]["map"]
        if self.timer is not None:
            self.timer.interval = self.refresh_seconds
        self.plan_counts = {test["name"]: len(generate_points(test)) for test in project["tests"]}
        self.run_selection &= {test["name"] for test in project["tests"]}
        self._rebuild_config_rows()
        self._refresh_result_options()
        self._sync_start_button()

    def _pick_initial_name(self) -> str | None:
        for name in CONTROL.snapshot()["names"]:
            if name in self.store_names:
                return name
        if self.store_names:
            return self.store_names[0]
        configured = [test["name"] for test in self.project["tests"]]
        return configured[0] if configured else None

    def _put(self, element, text: str) -> None:
        if getattr(element, "_lw_text", None) != text:
            element.set_text(text)
            element._lw_text = text

    def _flag(self, element, enabled: bool) -> None:
        if getattr(element, "_lw_enabled", None) != enabled:
            element.enable() if enabled else element.disable()
            element._lw_enabled = enabled

    def _visible(self, element, visible: bool) -> None:
        if getattr(element, "_lw_visible", None) != visible:
            element.set_visibility(visible)
            element._lw_visible = visible

    def _running_names(self) -> set:
        snapshot = CONTROL.snapshot()
        return set(snapshot["names"]) if snapshot["state"] in BUSY_STATES else set()

    def _render_editor_guard(self, snapshot) -> None:
        if self.editor_name_input is None or self.editing_name is None:
            return
        blocked = snapshot["state"] in BUSY_STATES and self.editing_name in snapshot["names"]
        if self.editor_guard_blocked == blocked:
            return
        self.editor_guard_blocked = blocked
        self._flag(self.editor_name_input, not blocked)
        if self.editor_delete_button is not None:
            self._flag(self.editor_delete_button, not blocked)
        if self.editor_name_guard is not None:
            self._visible(self.editor_name_guard, blocked)
        if self.editor_delete_guard is not None:
            self._visible(self.editor_delete_guard, blocked)

    def _bind_dismiss(self, select, token: str) -> None:
        """Guarantee the select popup closes on selection, outside click and Escape."""
        select.classes(f"lw-select-{token}")
        select.on(
            "popup-show",
            handler=lambda event: select.run_method("hidePopup") if event.args == "lw-hide" else None,
            js_handler=DISMISS_JS % {"token": token},
        )
        select.on_value_change(lambda: select.run_method("hidePopup"))
        select.on("popup-hide", js_handler="() => { if (window.__lwSelectDismiss) window.__lwSelectDismiss(); }")

    def _register_expansion(self, expansion, token: str) -> None:
        """One-open-at-a-time group plus outside-click/Escape collapse for an expansion."""
        expansion.classes(f"lw-expand-{token}")
        expansion.on_value_change(
            lambda event, exp=expansion, tok=token: self._expansion_changed(exp, tok, bool(event.value))
        )
        self.expansions.append((expansion, token))

    def _collapse_expansion(self, expansion) -> None:
        expansion.value = False

    def _expansion_changed(self, expansion, token: str, opened: bool) -> None:
        self.expansions = [(exp, tok) for exp, tok in self.expansions if not exp.is_deleted]
        if opened:
            for other, _ in self.expansions:
                if other is not expansion and other.value:
                    other.value = False
            ui.run_javascript(EXPAND_JS % {"token": token})
        else:
            ui.run_javascript(EXPAND_CLEANUP_JS)

    # --------------------------------------------------------------- chrome

    def _build_header(self) -> None:
        with ui.row().classes("lw-header w-full items-center gap-3"):
            ui.label("Land or Water").classes("lw-title")
            ui.label(self.t("subtitle")).classes("lw-subtitle")
            ui.space()
            ui.label(self.t("language")).classes("lw-sub")
            lang_select = ui.select(list(self.languages), value=self.lang, on_change=self._switch_language)
            lang_select.classes("w-28").props("dense outlined")
            self._bind_dismiss(lang_select, "language")

    def _switch_language(self, event) -> None:
        value = event.value
        if value == self.lang or value not in self.languages:
            return
        project = load_config(CONFIG_PATH)
        project["ui"]["language"] = value
        save_config(CONFIG_PATH, project)
        core.app.config.language = LOCALES.get(value, "en-US")
        ui.navigate.reload()

    def _build_console(self) -> None:
        with ui.element("div").classes("lw-console w-full").props("id=runtime-console"):
            with ui.row().classes("w-full items-center gap-3"):
                self.state_label = ui.label(self.t("state_idle")).classes("lw-state").props("id=runtime-state")
                self.start_button = ui.button(self.t("start_run", count=0), on_click=self._start).props(
                    "color=primary"
                )
                self.stop_button = ui.button(self.t("stop"), on_click=self._stop, color="negative")
                self.names_label = ui.label(self.t("batch_none")).classes("lw-sub")
                self.elapsed_label = ui.label(self.t("elapsed", seconds="0.0")).classes("lw-sub")
            with ui.row().classes("w-full items-center gap-4"):
                self.progress_label = ui.label(self.t("total_none")).classes("lw-mono")
                with ui.element("div").classes("lw-progress"):
                    self.progress_bar = ui.linear_progress(value=0, show_value=False)
                self.rate_label = ui.label(self.t("rate_none")).classes("lw-mono")
                self.eta_label = ui.label(self.t("eta_value", eta=self.t("eta_none"))).classes("lw-mono")
                self.failed_label = ui.label(self.t("failures", count=0)).classes("lw-mono")
                self.skipped_label = ui.label(self.t("cached_skipped", count=0)).classes("lw-mono")
                self.tokens_label = ui.label(self.t("tokens_total", value="—")).classes("lw-mono")
            self.error_label = ui.label("").classes("lw-error")
            self.error_label.set_visibility(False)
            self.error_label._lw_visible = False
            self.console_tests = ui.column().classes("w-full gap-1")
            self._build_console_rows([])

    def _build_console_rows(self, names) -> None:
        self.console_names = tuple(names)
        self.console_tests.clear()
        self.console_test_rows.clear()
        with self.console_tests:
            if not names:
                ui.label(self.t("console_empty")).classes("lw-sub")
                return
            with ui.grid(columns=CONSOLE_GRID).classes("w-full lw-head gap-x-2"):
                for key in (
                    "col_test",
                    "col_progress",
                    "col_percent",
                    "col_rate",
                    "col_eta",
                    "col_failures",
                    "col_latency",
                    "col_tokens",
                ):
                    ui.label(self.t(key))
            for name in names:
                with ui.grid(columns=CONSOLE_GRID).classes("w-full lw-row gap-x-2"):
                    ui.label(name).classes("lw-name")
                    self.console_test_rows[name] = {
                        "progress": ui.label(self.t("preparing")),
                        "percent": ui.label(self.t("preparing")),
                        "rate": ui.label("—"),
                        "eta": ui.label("—"),
                        "failed": ui.label("0"),
                        "latency": ui.label("—"),
                        "tokens": ui.label("—"),
                    }

    def _select_tab(self, key: str) -> None:
        for name, panel in self.panels.items():
            panel.set_visibility(name == key)
        for name, button in self.tab_buttons.items():
            if name == key:
                button.props("color=primary")
            else:
                button.props("color=grey-7")

    # ------------------------------------------------------- config panel

    def _build_config_panel(self) -> None:
        with ui.card().classes("lw-panel w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(self.t("config_title")).classes("lw-h2")
                ui.button(self.t("add_config"), on_click=self._new_config).props("flat")
            ui.label(self.t("config_hint")).classes("text-xs text-gray-500")
            self.config_table = ui.column().classes("w-full gap-0").props("id=config-table")
            self._rebuild_config_rows()
            ui.separator()
            self.editor_container = ui.column().classes("w-full gap-3").props("id=config-editor")
            self._build_editor()
            ui.separator()
            self._build_io_section()

    def _rebuild_config_rows(self) -> None:
        self.config_table.clear()
        self.config_rows.clear()
        store = Store(self.database)
        try:
            counts = {
                test["name"]: store.progress(test["name"])["count"] for test in self.project["tests"]
            }
        finally:
            store.close()
        self.config_counts = counts
        with self.config_table:
            with ui.grid(columns=CONFIG_GRID).classes("w-full lw-head gap-x-2"):
                for key in ("col_pick", "col_name", "col_model", "col_protocol", "col_plan", "col_cached", "col_actions"):
                    ui.label(self.t(key))
            if not self.project["tests"]:
                ui.label(self.t("config_empty")).classes("lw-sub py-2")
            for test in self.project["tests"]:
                name = test["name"]
                with ui.grid(columns=CONFIG_GRID).classes("w-full lw-row gap-x-2").props(
                    f"id=config-row-{_safe(name)}"
                ):
                    ui.checkbox(
                        value=name in self.run_selection,
                        on_change=lambda event, selected=name: self._toggle_run(selected, event.value),
                    ).props(f"id=config-check-{_safe(name)}")
                    ui.label(name).classes("lw-name")
                    ui.label(test["model"])
                    ui.label(test["protocol"])
                    ui.label(str(self.plan_counts.get(name, "?")))
                    count_label = ui.label(str(counts[name]))
                    with ui.row().classes("gap-1"):
                        ui.button(
                            self.t("edit"), on_click=lambda selected=name: self._open_editor(selected)
                        ).props("flat dense")
                        ui.button(self.t("ping"), on_click=self._make_ping(name)).props("flat dense")
                self.config_rows[name] = {"count": count_label}

    def _toggle_run(self, name: str, selected: bool) -> None:
        if selected:
            self.run_selection.add(name)
        else:
            self.run_selection.discard(name)
        self._sync_start_button()

    def _sync_start_button(self) -> None:
        busy = CONTROL.snapshot()["state"] in BUSY_STATES
        self._put(self.start_button, self.t("start_run", count=len(self.run_selection)))
        self._flag(self.start_button, not busy and bool(self.run_selection))

    def _start(self) -> None:
        self._reload()
        names = [test["name"] for test in self.project["tests"] if test["name"] in self.run_selection]
        if not names:
            return
        CONTROL.start(ROOT, self.project, names)
        self._render_console(CONTROL.snapshot(), {"progress": {}, "batch": {}}, prepared=False)

    def _stop(self) -> None:
        CONTROL.stop()
        self._put(self.state_label, self.t("state_stopping"))
        self._flag(self.stop_button, False)

    # ------------------------------------------------------- editor

    def _find_test(self, name: str) -> dict:
        for test in self.project["tests"]:
            if test["name"] == name:
                return test
        raise KeyError(name)

    def _open_editor(self, name: str) -> None:
        self._reload()
        self.editing_name = name
        self.draft_is_new = False
        self.draft = copy.deepcopy(self._find_test(name))
        self._build_editor()

    def _open_draft(self, draft: dict) -> None:
        self.editing_name = None
        self.draft_is_new = True
        self.draft = draft
        self._build_editor()

    def _build_editor(self) -> None:
        self.editor_widgets = {}
        self.editor_heading = None
        self.editor_feedback = None
        self.editor_name_input = None
        self.editor_delete_button = None
        self.editor_name_guard = None
        self.editor_delete_guard = None
        self.editor_guard_blocked = None
        self.editor_container.clear()
        draft = self.draft
        with self.editor_container:
            if draft is None:
                ui.label(self.t("editor_none")).classes(
                    "lw-sub"
                )
                return
            self.editor_heading = ui.label("").classes("lw-h3")
            self._refresh_editor_heading()
            with ui.grid(columns="repeat(2, minmax(0,1fr))").classes("w-full gap-2 lw-2col"):
                self.editor_name_input = ui.input(
                    "name", value=draft["name"], on_change=self._refresh_editor_heading
                ).classes("w-full")
                self.editor_widgets["name"] = self.editor_name_input
                self.editor_widgets["base_url"] = ui.input("base_url", value=draft["base_url"]).classes("w-full")
                self.editor_widgets["api_key"] = ui.input(
                    "api_key", value=draft["api_key"], password=True, password_toggle_button=True
                ).classes("w-full")
                self.editor_widgets["model"] = ui.input("model", value=draft["model"]).classes("w-full")
                protocol_select = ui.select(
                    list(PROTOCOLS), label="protocol", value=draft["protocol"]
                ).classes("w-full")
                self._bind_dismiss(protocol_select, "protocol")
                self.editor_widgets["protocol"] = protocol_select
                envelope_select = ui.select(
                    list(ENVELOPES), label=self.t("f_envelope"), value=draft["response_envelope"]
                ).classes("w-full")
                self._bind_dismiss(envelope_select, "response-envelope")
                self.editor_widgets["response_envelope"] = envelope_select
            self.editor_name_guard = ui.label(self.t("guard_rename_running")).classes("lw-warn")
            self.editor_name_guard.set_visibility(False)
            self.editor_name_guard._lw_visible = False
            with ui.expansion(self.t("advanced"), value=False).classes("w-full") as advanced:
                self._register_expansion(advanced, "advanced")
                ui.button(
                    self.t("collapse"), on_click=lambda exp=advanced: self._collapse_expansion(exp)
                ).props("flat dense")
                limits = draft["limits"]
                with ui.grid(columns="repeat(3, minmax(0,1fr))").classes("w-full gap-2 lw-3col"):
                    self.editor_widgets["rpm"] = ui.number(
                        "limits.rpm", value=limits["rpm"], min=1, step=1
                    ).classes("w-full")
                    self.editor_widgets["max_in_flight"] = ui.number(
                        "limits.max_in_flight", value=limits["max_in_flight"], min=1, step=1
                    ).classes("w-full")
                    self.editor_widgets["tpm"] = ui.input(
                        self.t("f_tpm"), value=_as_text(limits["tpm"]), placeholder="null"
                    ).classes("w-full")
                    self.editor_widgets["retries"] = ui.number(
                        "retries", value=draft["retries"], min=0, step=1
                    ).classes("w-full")
                    self.editor_widgets["retry_delay_seconds"] = ui.number(
                        "retry_delay_seconds", value=draft["retry_delay_seconds"], min=0, step=0.5
                    ).classes("w-full")
                    self.editor_widgets["timeout_seconds"] = ui.number(
                        "timeout_seconds", value=draft["timeout_seconds"], min=1, step=1
                    ).classes("w-full")
                    self.editor_widgets["max_output_tokens"] = ui.input(
                        self.t("f_max_output"),
                        value=_as_text(draft["max_output_tokens"]),
                        placeholder="null",
                    ).classes("w-full")
                    self.editor_widgets["temperature"] = ui.input(
                        self.t("f_temperature"), value=_as_text(draft["temperature"]), placeholder="null"
                    ).classes("w-full")
                    self.editor_widgets["proxy"] = ui.input(
                        self.t("f_proxy"), value=_as_text(draft["proxy"]), placeholder="null"
                    ).classes("w-full")
                    self.editor_widgets["step_degrees"] = ui.number(
                        "step_degrees", value=draft["step_degrees"], min=0.1, step=0.5
                    ).classes("w-full")
                    self.editor_widgets["point_count"] = ui.input(
                        self.t("f_point_count"), value=_as_text(draft["point_count"]), placeholder="null"
                    ).classes("w-full")
                    self.editor_widgets["seed"] = ui.number("seed", value=draft["seed"], step=1).classes("w-full")
                self.editor_widgets["prompt"] = ui.textarea(
                    self.t("f_prompt"), value=draft["prompt"]
                ).classes("w-full").props("autogrow")
                ui.separator()
                ui.label(self.t("params_title")).classes("lw-sub")
                ui.label(self.t("params_hint")).classes("lw-sub")
                self.params_container = ui.column().classes("w-full gap-1")
                ui.button(self.t("params_add"), on_click=self._params_add).props("flat dense")
                self._render_params()
            with ui.row().classes("gap-2 items-center"):
                ui.button(self.t("save_config"), on_click=self._save_editor).props("color=primary")
                ui.button(self.t("cancel"), on_click=self._cancel_editor).props("flat")
                ui.button(self.t("copy_config"), on_click=self._copy_draft).props("flat")
                if not self.draft_is_new:
                    self.editor_delete_button = ui.button(
                        self.t("delete_config"), on_click=self._confirm_delete, color="negative"
                    ).props("flat")
            self.editor_delete_guard = ui.label(self.t("guard_delete_config_running")).classes("lw-warn")
            self.editor_delete_guard.set_visibility(False)
            self.editor_delete_guard._lw_visible = False
            self.editor_feedback = ui.label("").classes("lw-sub")
        self._render_editor_guard(CONTROL.snapshot())

    def _render_params(self) -> None:
        self.params_container.clear()
        self.param_rows = []
        entries = self.draft["request_parameters"]
        with self.params_container:
            if not entries:
                ui.label(self.t("params_empty")).classes("lw-sub")
                return
            for index, entry in enumerate(entries):
                self._render_param_row(index, entry)

    def _render_param_row(self, index: int, entry: dict) -> None:
        kind = entry["type"]
        with ui.grid(columns=PARAM_GRID).classes("w-full lw-row gap-x-2"):
            key_input = ui.input(value=entry["key"], placeholder="reasoning.effort").classes("w-full").props(
                "dense"
            )
            type_select = ui.select(
                list(PARAM_TYPES), value=kind, label=self.t("params_type")
            ).classes("w-full").props("dense")
            self._bind_dismiss(type_select, f"param-type-{index}")
            if kind == "boolean":
                value_widget = ui.checkbox(value=bool(entry["value"]))
            elif kind == "json":
                value_widget = ui.textarea(
                    value=json.dumps(entry["value"], ensure_ascii=False)
                ).classes("w-full").props("dense autogrow")
            else:
                value_widget = ui.input(
                    label=self.t("params_value"), value=_as_text(entry["value"])
                ).classes("w-full").props("dense")
            ui.button(self.t("params_remove"), on_click=lambda i=index: self._params_remove(i)).props(
                "flat dense"
            )
        type_select.on_value_change(lambda event, i=index: self._params_type_changed(i, event.value))
        self.param_rows.append({"key": key_input, "type": type_select, "value": value_widget})

    def _collect_params(self) -> list:
        entries = []
        for row in self.param_rows:
            kind = row["type"].value
            raw = row["value"]
            if kind == "boolean":
                value = bool(raw.value)
            elif kind == "json":
                value = json.loads(raw.value or "null")
            elif kind == "number":
                text = (raw.value or "").strip()
                value = float(text) if ("." in text or "e" in text.lower()) else int(text)
            else:
                value = raw.value
            entries.append({"key": (row["key"].value or "").strip(), "type": kind, "value": value})
        self.draft["request_parameters"] = entries
        return entries

    def _params_add(self) -> None:
        try:
            self._collect_params()
        except (TypeError, ValueError) as exc:
            self._set_feedback(self.t("err_format", error=exc))
            return
        self.draft["request_parameters"].append({"key": "", "type": "string", "value": ""})
        self._render_params()

    def _params_remove(self, index: int) -> None:
        try:
            self._collect_params()
        except (TypeError, ValueError) as exc:
            self._set_feedback(self.t("err_format", error=exc))
            return
        del self.draft["request_parameters"][index]
        self._render_params()

    def _params_type_changed(self, index: int, kind: str) -> None:
        try:
            self._collect_params()
        except (TypeError, ValueError) as exc:
            self._set_feedback(self.t("err_format", error=exc))
            return
        entry = self.draft["request_parameters"][index]
        entry["type"] = kind
        entry["value"] = PARAM_DEFAULTS[kind]
        self._render_params()

    def _refresh_editor_heading(self) -> None:
        if self.editor_heading is None or self.draft is None:
            return
        widget = self.editor_widgets.get("name")
        name = widget.value if widget is not None else self.draft.get("name", "")
        suffix = self.t("editing_new_suffix") if self.draft_is_new else ""
        self._put(self.editor_heading, self.t("editing", name=name or self.t("unnamed"), suffix=suffix))

    def _set_feedback(self, text: str) -> None:
        if self.editor_feedback is not None:
            self.editor_feedback.set_text(text)

    def _collect_editor(self) -> dict:
        widgets = self.editor_widgets
        return {
            "name": (widgets["name"].value or "").strip(),
            "base_url": (widgets["base_url"].value or "").strip(),
            "api_key": widgets["api_key"].value,
            "model": (widgets["model"].value or "").strip(),
            "protocol": widgets["protocol"].value,
            "response_envelope": widgets["response_envelope"].value,
            "proxy": _optional_text(widgets["proxy"].value),
            "limits": {
                "rpm": _int(widgets["rpm"].value),
                "max_in_flight": _int(widgets["max_in_flight"].value),
                "tpm": _optional_int(widgets["tpm"].value),
            },
            "retries": _int(widgets["retries"].value),
            "retry_delay_seconds": _number(widgets["retry_delay_seconds"].value),
            "timeout_seconds": _number(widgets["timeout_seconds"].value),
            "max_output_tokens": _optional_int(widgets["max_output_tokens"].value),
            "temperature": _optional_float(widgets["temperature"].value),
            "prompt": widgets["prompt"].value,
            "step_degrees": _number(widgets["step_degrees"].value),
            "point_count": _optional_int(widgets["point_count"].value),
            "seed": _int(widgets["seed"].value),
            "request_parameters": self._collect_params(),
        }

    async def _save_editor(self) -> None:
        if self.draft is None:
            return
        try:
            draft = self._collect_editor()
        except (TypeError, ValueError) as exc:
            self._set_feedback(self.t("err_format", error=exc))
            return
        if not draft["name"]:
            self._set_feedback(self.t("err_name_required"))
            return
        project = load_config(CONFIG_PATH)
        names = [test["name"] for test in project["tests"]]
        editing = self.editing_name
        if draft["name"] in names and draft["name"] != editing:
            self._set_feedback(self.t("err_name_exists", name=draft["name"]))
            return
        renaming = editing is not None and editing in names and draft["name"] != editing
        if renaming:
            if editing in self._running_names():
                self._set_feedback(self.t("guard_rename_running"))
                return
            count, store_names = await run.io_bound(_probe_name, self.database, editing)
            if draft["name"] in store_names:
                self._set_feedback(self.t("err_result_exists", name=draft["name"]))
                return
            if count:
                self._rename_config_dialog(project, draft, editing, count)
                return
        if editing is not None and editing in names:
            project["tests"][names.index(editing)] = draft
        else:
            project["tests"].append(draft)
        self.editing_name = draft["name"]
        self.draft_is_new = False
        self.draft = draft
        self._commit(project)
        self._build_editor()
        self._set_feedback(self.t("saved", name=draft["name"]))

    def _rename_config_dialog(self, project, draft, old: str, count: int) -> None:
        target = draft["name"]
        with ui.dialog() as dialog, ui.card().classes("max-w-xl"):
            ui.label(self.t("rename_config_title", old=old, target=target)).classes("lw-h3")
            sync = ui.checkbox(self.t("rename_sync"), value=True)
            hint = ui.label(self.t("rename_will", count=f"{count:,}"))

            def toggle() -> None:
                if sync.value:
                    hint.set_text(self.t("rename_will", count=f"{count:,}"))
                else:
                    hint.set_text(self.t("rename_stay", count=f"{count:,}", old=old))

            async def run_save() -> None:
                if sync.value:
                    rows = await run.io_bound(_rename_results, self.database, old, target)
                else:
                    rows = 0
                await self._commit_config_rename(project, draft, old, target, rows)
                if rows:
                    ui.notify(self.t("notify_renamed", rows=rows, old=old, target=target))
                else:
                    ui.notify(self.t("notify_saved_stay", target=target, count=count, old=old))
                dialog.close()

            with ui.row().classes("gap-2 justify-end w-full"):
                ui.button(self.t("cancel"), on_click=dialog.close).props("flat")
                ui.button(self.t("confirm_save"), on_click=lambda: run_save()).props("color=primary")
            sync.on_value_change(lambda: toggle())
        dialog.open()

    async def _commit_config_rename(self, project, draft, old: str, target: str, rows: int) -> None:
        names = [test["name"] for test in project["tests"]]
        project["tests"][names.index(old)] = draft
        if rows:
            if old in self.cards:
                self.cards.pop(old)["element"].delete()
                self.stamps.pop(old, None)
            if old in self.run_selection:
                self.run_selection.discard(old)
                self.run_selection.add(target)
            for collection in (self.selected_results, self._desired_results):
                if old in collection:
                    collection[collection.index(old)] = target
            self.store_names = [target if item == old else item for item in self.store_names]
            self.result_names = tuple(self.store_names)
        self.editing_name = target
        self.draft_is_new = False
        self.draft = draft
        self._commit(project)
        if rows:
            await self._sync_cards()
        self._build_editor()
        self._set_feedback(self.t("saved", name=target))

    def _cancel_editor(self) -> None:
        self._reload()
        names = [test["name"] for test in self.project["tests"]]
        if self.editing_name is not None and self.editing_name in names:
            self.draft = copy.deepcopy(self._find_test(self.editing_name))
            self.draft_is_new = False
            self._build_editor()
            self._set_feedback(self.t("discarded"))
        else:
            self.draft = None
            self.editing_name = None
            self.draft_is_new = False
            self._build_editor()

    def _unique_name(self, base: str) -> str:
        names = {test["name"] for test in self.project["tests"]}
        if base not in names:
            return base
        index = 2
        while f"{base}_{index}" in names:
            index += 1
        return f"{base}_{index}"

    def _new_config(self) -> None:
        self._reload()
        draft = copy.deepcopy(self.project["ui"]["new_test"])
        draft["name"] = self._unique_name(draft["name"])
        self._open_draft(draft)
        self._set_feedback(self.t("new_draft"))

    def _copy_draft(self) -> None:
        if self.draft is None:
            return
        draft = copy.deepcopy(self.draft)
        draft["name"] = self._unique_name(f"{draft['name']}_copy")
        self._open_draft(draft)
        self._set_feedback(self.t("copied", name=draft["name"]))

    def _confirm_delete(self) -> None:
        name = self.editing_name
        if name is None:
            return
        if name in self._running_names():
            self._set_feedback(self.t("guard_delete_config_running"))
            return
        with ui.dialog() as dialog, ui.card().classes("max-w-xl"):
            ui.label(self.t("delete_config_title", name=name)).classes("lw-h3")
            ui.label(
                self.t("delete_config_body")
            ).classes("text-sm")
            with ui.row().classes("gap-2 justify-end w-full"):
                ui.button(self.t("cancel"), on_click=dialog.close).props("flat")
                ui.button(self.t("confirm_delete"), color="negative", on_click=lambda: self._delete(name, dialog))
        dialog.open()

    def _delete(self, name: str, dialog) -> None:
        project = load_config(CONFIG_PATH)
        project["tests"] = [test for test in project["tests"] if test["name"] != name]
        self.editing_name = None
        self.draft = None
        self.draft_is_new = False
        self._commit(project)
        self._build_editor()
        self._set_feedback(self.t("deleted_config", name=name))
        dialog.close()

    # ------------------------------------------------------- connectivity

    def _make_ping(self, name: str):
        async def handler() -> None:
            await self._ping(name)

        return handler

    async def _ping(self, name: str) -> None:
        self._reload()
        tests = {test["name"]: test for test in self.project["tests"]}
        if name not in tests:
            ui.notify(self.t("config_missing", name=name))
            return
        test = copy.deepcopy(tests[name])
        with ui.dialog() as dialog, ui.card().classes("max-w-4xl w-full").props(f"id=ping-{_safe(name)}"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(self.t("ping_title", name=name)).classes("lw-h3")
                ui.button(self.t("close"), on_click=dialog.close).props("flat dense")
            ui.label(self.t("ping_hint")).classes(
                "text-xs text-gray-500"
            )
            status = ui.label(self.t("ping_status")).classes("lw-ping-status")
            detail = ui.column().classes("gap-1 w-full")
        dialog.open()
        result = await test_connection(test)
        if result["usable"]:
            status.set_text(
                self.t(
                    "ping_ok",
                    latency=f"{result['latency_seconds']:.3f}",
                    lon=f"{result['longitude']:.4f}",
                    lat=f"{result['latitude']:.4f}",
                )
            )
            status.classes(add="lw-ok")
        else:
            status.set_text(self.t("ping_bad", error=result["error"]))
            status.classes(add="lw-bad")
        with detail:
            if result["answer"]:
                ui.label(f"answer：{result['answer']}").classes("text-sm")
            ui.label(self.t("raw_request")).classes("lw-sub")
            with ui.element("div").classes("lw-raw w-full"):
                ui.label(result["request_json"]).classes("lw-mono")
            ui.label(self.t("raw_response")).classes("lw-sub")
            with ui.element("div").classes("lw-raw w-full"):
                ui.label(result["response_json"] or self.t("no_body")).classes("lw-mono")

    # ------------------------------------------------------- import / export

    def _build_io_section(self) -> None:
        with ui.expansion(self.t("io_title"), value=False).classes("w-full") as io_exp:
            self._register_expansion(io_exp, "io")
            ui.button(
                self.t("collapse"), on_click=lambda exp=io_exp: self._collapse_expansion(exp)
            ).props("flat dense")
            with ui.row().classes("items-center gap-3"):
                ui.upload(on_upload=self._import_config, auto_upload=True, label=self.t("io_upload")).props(
                    "accept=.yaml,.yml,.json flat bordered"
                ).classes("lw-upload")
                ui.button(self.t("export_yaml"), on_click=lambda: self._export_config("yaml")).props("flat")
                ui.button(self.t("export_json"), on_click=lambda: self._export_config("json")).props("flat")
            self.io_feedback = ui.label(
                self.t("io_hint")
            ).classes("lw-sub")
        with ui.expansion(self.t("root_title"), value=False).classes("w-full") as root_exp:
            self._register_expansion(root_exp, "root")
            ui.button(
                self.t("collapse"), on_click=lambda exp=root_exp: self._collapse_expansion(exp)
            ).props("flat dense")
            self.root_editor = ui.textarea(value=self._root_text()).classes("w-full").props(
                'autogrow input-style="font-family: monospace; font-size: 12px"'
            )
            with ui.row().classes("gap-2 items-center"):
                ui.button(self.t("root_apply"), on_click=self._apply_root).props("flat")
                self.root_feedback = ui.label(self.t("root_hint"))

    def _root_text(self) -> str:
        return yaml.safe_dump(
            {"paths": self.project["paths"], "ui": self.project["ui"]}, allow_unicode=True, sort_keys=False
        )

    def _export_config(self, fmt: str) -> None:
        self._reload()
        filename = "project.yaml" if fmt == "yaml" else "project.json"
        media = "text/yaml" if fmt == "yaml" else "application/json"
        ui.download.content(config_text(self.project, fmt), filename=filename, media_type=media)

    async def _import_config(self, event) -> None:
        text = await event.file.text()
        try:
            imported = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self.io_feedback.set_text(self.t("import_parse_error", error=exc))
            return
        if not isinstance(imported, dict) or not {"paths", "ui", "tests"} <= set(imported):
            self.io_feedback.set_text(self.t("import_schema_error"))
            return
        self.run_selection = set()
        self.editing_name = None
        self.draft = None
        self.draft_is_new = False
        self._commit(imported)
        self._build_editor()
        self.root_editor.value = self._root_text()
        self.io_feedback.set_text(
            self.t("import_ok", count=len(imported["tests"]))
        )

    def _apply_root(self) -> None:
        try:
            parsed = yaml.safe_load(self.root_editor.value)
            paths = parsed["paths"]
            ui_config = parsed["ui"]
        except (yaml.YAMLError, TypeError, KeyError) as exc:
            self.root_feedback.set_text(self.t("root_error", error=exc))
            return
        project = load_config(CONFIG_PATH)
        project["paths"] = paths
        project["ui"] = ui_config
        self._commit(project)
        self.root_editor.value = self._root_text()
        self.root_feedback.set_text(self.t("root_saved"))

    # ------------------------------------------------------- results panel

    def _build_results_panel(self) -> None:
        with ui.card().classes("lw-panel w-full"):
            ui.label(self.t("results_title")).classes("lw-h2")
            ui.label(
                self.t("results_hint")
            ).classes("text-xs text-gray-500")
            self.result_select = ui.select(
                self._result_options(),
                label=self.t("results_label"),
                value=self.selected_results,
                multiple=True,
                on_change=self._on_result_select,
            ).classes("w-full max-w-3xl").props("use-chips id=result-select")
            self._bind_dismiss(self.result_select, "result-set")
            self.result_select.on("popup-hide", self._flush_result_options)
        self.cards_container = ui.column().classes("w-full gap-4")

    def _result_options(self) -> dict:
        options = {test["name"]: test["name"] for test in self.project["tests"]}
        for name in self.store_names:
            if name not in options:
                options[name] = self.t("archived", name=name)
        return options

    def _refresh_result_options(self) -> None:
        options = self._result_options()
        value = [name for name in self.selected_results if name in options]
        if self.result_select.is_showing_popup:
            self._pending_options = (options, value)
            return
        self.result_select.set_options(options, value=value)

    def _flush_result_options(self) -> None:
        if self._pending_options is None:
            return
        options, value = self._pending_options
        self._pending_options = None
        self.result_select.set_options(options, value=value)

    async def _on_result_select(self, event) -> None:
        self.result_select.run_method("hidePopup")
        self._desired_results = list(event.value or [])
        await self._sync_cards()

    async def _sync_cards(self) -> None:
        """Reconcile cards with the latest selection; serialized against fast re-selection."""
        async with self.cards_lock:
            while True:
                desired = list(self._desired_results)
                for name in list(self.cards):
                    if name not in desired:
                        self.cards.pop(name)["element"].delete()
                        self.stamps.pop(name, None)
                self.selected_results = desired
                new = [name for name in desired if name not in self.cards]
                if not new:
                    break
                payload = await run.io_bound(_load_results, self.database, tuple(new))
                if payload is None:
                    return
                for name in new:
                    if name in self.cards or name not in self._desired_results:
                        continue
                    self._create_card(name, payload[name])
                if list(self._desired_results) == desired:
                    break

    def _create_card(self, name: str, payload: dict) -> None:
        card = {"refreshes": 0}
        with self.cards_container:
            element = ui.card().classes("lw-card w-full").props(f"id=result-{_safe(name)}")
            with element:
                with ui.row().classes("items-center justify-between w-full gap-4"):
                    with ui.column().classes("gap-0"):
                        ui.label(name).classes("lw-h3")
                        card["version"] = ui.label("").classes("lw-sub")
                    with ui.row().classes("gap-1"):
                        card["rename"] = ui.button(self.t("card_rename"), on_click=self._make_result_rename(name)).props(
                            "flat dense"
                        )
                        card["delete"] = ui.button(
                            self.t("card_delete"), on_click=self._make_result_delete(name), color="negative"
                        ).props("flat dense")
                card["rename_guard"] = ui.label(self.t("guard_rename_running")).classes("lw-warn")
                card["rename_guard"].set_visibility(False)
                card["rename_guard"]._lw_visible = False
                card["delete_guard"] = ui.label(self.t("guard_delete_result_running")).classes("lw-warn")
                card["delete_guard"].set_visibility(False)
                card["delete_guard"]._lw_visible = False
                card["stats"] = ui.label("").classes("lw-mono lw-stats")
                card["plot"] = ui.plotly(self._figure(name, payload["rows"])).classes("w-full").props(
                    f"id=result-plot-{_safe(name)}"
                )
                ui.label(
                    self.t("map_caption")
                ).classes("lw-sub")
                with ui.row().classes("gap-2"):
                    ui.button(self.t("export_png"), on_click=lambda selected=name: self._export_png(selected)).props(
                        "flat dense"
                    )
                    for kind in ("csv", "json", "html"):
                        ui.button(self.t("export_kind", kind=kind.upper()), on_click=self._make_export(name, kind)).props(
                            "flat dense"
                        )
        card["element"] = element
        self.cards[name] = card
        self.stamps[name] = payload["stamp"]
        self._render_card(name, payload["rows"], payload["stamp"], refreshed=False)
        self._render_card_guards(CONTROL.snapshot())

    def _step_for(self, name: str):
        """Grid width for a result set: the configured test's step, else the root history step."""
        for test in self.project["tests"]:
            if test["name"] == name:
                return test["step_degrees"], False
        return self.map_config["history_step_degrees"], True

    def _figure(self, name: str, rows) -> dict:
        step, history = self._step_for(name)
        note = self.t("map_history_note") if history else ""
        title = self.t("map_title", name=name, step=step, note=note)
        return _heatmap_figure(rows, step, self.map_config["colors"], title, name, map_labels(self.lang))

    def _stats_text(self, rows) -> str:
        summary = summarize(rows)
        return "\n".join(
            [
                " · ".join(
                    [
                        self.t("stats_total", value=f"{summary['total']:,}"),
                        self.t("stats_valid", value=f"{summary['valid']:,}"),
                        self.t("stats_invalid", value=f"{summary['invalid']:,}"),
                        self.t("stats_failed", value=f"{summary['failed']:,}"),
                        self.t("stats_correct", value=f"{summary['correct']:,}"),
                        self.t("stats_accuracy", value=f"{summary['accuracy']:.2%}"),
                    ]
                ),
                self.t(
                    "stats_tokens",
                    input=_fmt_int(summary["input_tokens"]),
                    output=_fmt_int(summary["output_tokens"]),
                    total=_fmt_int(summary["total_tokens"]),
                    reasoning=_fmt_int(summary["reasoning_tokens"]),
                    cached=_fmt_int(summary["cached_tokens"]),
                ),
                self.t(
                    "stats_latency",
                    mean=_fmt_float(summary["mean_latency_seconds"], 3),
                    p50=_fmt_float(summary["p50_latency_seconds"], 3),
                    p95=_fmt_float(summary["p95_latency_seconds"], 3),
                    tps=_fmt_float(summary["tps"], 2),
                ),
                self.t("stats_usage", count=f"{summary['usage_count']:,}"),
            ]
        )

    def _render_card(self, name: str, rows, stamp, refreshed: bool) -> None:
        card = self.cards[name]
        if refreshed:
            card["refreshes"] += 1
            card["plot"].update_figure(self._figure(name, rows))
        count, latest = stamp
        step, history = self._step_for(name)
        note = self.t("version_note_history") if history else ""
        self._put(
            card["version"],
            self.t(
                "version",
                count=f"{count:,}",
                latest=latest or "—",
                refreshes=card["refreshes"],
                step=step,
                note=note,
            ),
        )
        self._put(card["stats"], self._stats_text(rows))

    def _render_card_guards(self, snapshot) -> None:
        running = frozenset(snapshot["names"]) if snapshot["state"] in BUSY_STATES else frozenset()
        state = (frozenset(self.cards), running)
        if self.cards_guard_state == state:
            return
        self.cards_guard_state = state
        for name, card in self.cards.items():
            blocked = name in running
            self._flag(card["rename"], not blocked)
            self._flag(card["delete"], not blocked)
            self._visible(card["rename_guard"], blocked)
            self._visible(card["delete_guard"], blocked)

    def _make_result_rename(self, name: str):
        async def handler() -> None:
            count, names = await run.io_bound(_probe_name, self.database, name)
            self._result_rename_dialog(name, count, names)

        return handler

    def _result_rename_dialog(self, name: str, count: int, names: list) -> None:
        with ui.dialog() as dialog, ui.card().classes("max-w-xl"):
            ui.label(self.t("rename_result_title", name=name)).classes("lw-h3")
            ui.label(self.t("rename_result_body", count=f"{count:,}")).classes("lw-sub")
            target = ui.input(self.t("rename_result_new"), value=name).classes("w-full")
            feedback = ui.label("").classes("lw-warn")

            async def run_rename() -> None:
                value = (target.value or "").strip()
                rows = await run.io_bound(_rename_results, self.database, name, value)
                await self._apply_result_rename(name, value)
                ui.notify(self.t("notify_result_renamed", rows=rows, old=name, new=value))
                dialog.close()

            with ui.row().classes("gap-2 justify-end w-full"):
                ui.button(self.t("cancel"), on_click=dialog.close).props("flat")
                confirm = ui.button(self.t("rename_result_confirm"), on_click=lambda: run_rename()).props("color=primary")

            def check() -> None:
                value = (target.value or "").strip()
                if not value:
                    feedback.set_text(self.t("err_new_name_required"))
                    confirm.disable()
                elif value != name and value in names:
                    feedback.set_text(self.t("err_target_taken", name=value))
                    confirm.disable()
                else:
                    feedback.set_text("")
                    confirm.enable()

            target.on_value_change(lambda: check())
        check()
        dialog.open()

    async def _apply_result_rename(self, old: str, new: str) -> None:
        if old in self.cards:
            self.cards.pop(old)["element"].delete()
            self.stamps.pop(old, None)
        for collection in (self.selected_results, self._desired_results):
            if old in collection:
                collection[collection.index(old)] = new
        self.store_names = [new if item == old else item for item in self.store_names]
        self.result_names = tuple(self.store_names)
        self._refresh_result_options()
        await self._sync_cards()

    def _make_result_delete(self, name: str):
        async def handler() -> None:
            count, _names = await run.io_bound(_probe_name, self.database, name)
            self._result_delete_dialog(name, count)

        return handler

    def _result_delete_dialog(self, name: str, count: int) -> None:
        with ui.dialog() as dialog, ui.card().classes("max-w-xl"):
            ui.label(self.t("delete_result_title", name=name)).classes("lw-h3")
            ui.label(self.t("delete_result_body", count=f"{count:,}")).classes("lw-sub")

            async def run_delete() -> None:
                rows = await run.io_bound(_delete_results, self.database, name)
                await self._apply_result_delete(name)
                ui.notify(self.t("notify_result_deleted", name=name, rows=rows))
                dialog.close()

            with ui.row().classes("gap-2 justify-end w-full"):
                ui.button(self.t("cancel"), on_click=dialog.close).props("flat")
                ui.button(self.t("confirm_delete"), color="negative", on_click=lambda: run_delete())
        dialog.open()

    async def _apply_result_delete(self, name: str) -> None:
        if name in self.cards:
            self.cards.pop(name)["element"].delete()
            self.stamps.pop(name, None)
        self.selected_results = [item for item in self.selected_results if item != name]
        self._desired_results = [item for item in self._desired_results if item != name]
        self.store_names = [item for item in self.store_names if item != name]
        self.result_names = tuple(self.store_names)
        self._refresh_result_options()
        await self._sync_cards()

    def _make_export(self, name: str, kind: str):
        async def handler() -> None:
            await self._export(name, kind)

        return handler

    def _export_png(self, name: str) -> None:
        """Rasterize the persistent client figure at the configured width (plot area 2:1)."""
        card = self.cards.get(name)
        if card is None:
            return
        width = self.map_config["export_width"]
        card["plot"].run_plot_method(
            "downloadImage",
            {
                "format": "png",
                "filename": f"{name}_map",
                "width": width,
                "height": _png_height(width),
                "scale": 1,
            },
        )

    async def _export(self, name: str, kind: str) -> None:
        self._reload()
        if kind == "html":
            step, history = self._step_for(name)
            note = self.t("map_history_note") if history else ""
            title = self.t("map_title", name=name, step=step, note=note)
            path = await run.io_bound(
                _write_heatmap_html,
                self.database,
                self.exports_dir,
                name,
                step,
                dict(self.map_config["colors"]),
                title,
                self.lang,
            )
        else:
            files = await run.io_bound(_write_export, self.database, self.exports_dir, name)
            path = files[kind]
        ui.download.content(path.read_bytes(), filename=path.name, media_type=EXPORT_MEDIA[kind])

    # ------------------------------------------------------- live tick

    def _render_console(self, snapshot: dict, data: dict, prepared: bool = True) -> None:
        state = snapshot["state"]
        names = snapshot["names"]
        elapsed = snapshot["elapsed_seconds"]
        progress = data["progress"]
        batch = data["batch"]
        fallback = self.plan_counts if prepared else {}

        self._put(self.state_label, self.t(f"state_{state}"))
        self._put(self.names_label, self.t("batch_names", names="、".join(names)) if names else self.t("batch_none"))
        self._put(self.elapsed_label, self.t("elapsed", seconds=f"{elapsed:.1f}"))
        busy = state in BUSY_STATES
        self._put(self.start_button, self.t("start_run", count=len(self.run_selection)))
        self._flag(self.start_button, not busy and bool(self.run_selection))
        self._flag(self.stop_button, state == "running")
        if snapshot["error"]:
            self._put(self.error_label, self.t("run_error", error=snapshot["error"]))
        self._visible(self.error_label, bool(snapshot["error"]))

        if tuple(names) != self.console_names:
            self._build_console_rows(names)

        completed_total = 0
        total_total = 0
        written_total = 0
        failed_total = 0
        token_total = 0
        skipped_total = 0
        token_known = False
        any_total = False
        for name in names:
            stored = progress.get(name, {})
            written = batch.get(name, {}).get("count", 0)
            skipped = snapshot["skipped"].get(name, 0)
            total = snapshot["total"].get(name, fallback.get(name))
            if total:
                any_total = True
                total_total += total
                completed_total += min(skipped + written, total)
            written_total += written
            failed_total += stored.get("failed") or 0
            skipped_total += skipped
            if stored.get("total_tokens") is not None:
                token_total += stored["total_tokens"]
                token_known = True

        if not names:
            self._put(self.progress_label, self.t("total_none"))
        elif not any_total:
            self._put(self.progress_label, self.t("total_preparing"))
        else:
            percent = completed_total / total_total
            self._put(
                self.progress_label,
                self.t(
                    "total_progress",
                    completed=f"{completed_total:,}",
                    total=f"{total_total:,}",
                    percent=f"{percent:.1%}",
                ),
            )

        percent_value = completed_total / total_total if any_total and total_total else 0.0
        if getattr(self.progress_bar, "_lw_value", None) != percent_value:
            self.progress_bar.value = percent_value
            self.progress_bar._lw_value = percent_value

        rate = written_total / elapsed if elapsed > 0 and written_total else None
        if rate is not None:
            self._put(self.rate_label, self.t("rate_value", rate=f"{rate:.3f}"))
        elif not names:
            self._put(self.rate_label, self.t("rate_none"))
        elif state in BUSY_STATES:
            self._put(self.rate_label, self.t("rate_wait"))
        else:
            self._put(self.rate_label, self.t("rate_zero"))

        remaining = total_total - completed_total if any_total else None
        if remaining is None:
            eta = self.t("eta_none")
        elif remaining <= 0:
            eta = self.t("eta_zero")
        elif state in ("stopping", "stopped"):
            eta = self.t("eta_stopped")
        elif rate:
            eta = f"{remaining / rate:.0f} s"
        else:
            eta = self.t("eta_wait")
        self._put(self.eta_label, self.t("eta_value", eta=eta))
        self._put(self.failed_label, self.t("failures", count=f"{failed_total:,}"))
        self._put(self.skipped_label, self.t("cached_skipped", count=f"{skipped_total:,}"))
        self._put(
            self.tokens_label,
            self.t("tokens_total", value=_fmt_int(token_total if token_known else None)),
        )

        for name in names:
            row = self.console_test_rows.get(name)
            if row is None:
                continue
            stored = progress.get(name, {})
            written = batch.get(name, {}).get("count", 0)
            skipped = snapshot["skipped"].get(name, 0)
            total = snapshot["total"].get(name, fallback.get(name))
            completed = skipped + written
            if total:
                self._put(row["progress"], f"{min(completed, total):,}/{total:,}")
                self._put(row["percent"], f"{min(completed / total, 1.0):.1%}")
            else:
                self._put(row["progress"], self.t("preparing"))
                self._put(row["percent"], self.t("preparing"))
            per_rate = written / elapsed if elapsed > 0 and written else None
            if per_rate is not None:
                self._put(row["rate"], f"{per_rate:.3f}")
            elif state in BUSY_STATES:
                self._put(row["rate"], self.t("eta_wait"))
            else:
                self._put(row["rate"], "0")
            row_remaining = total - completed if total else None
            if row_remaining is None:
                row_eta = self.t("eta_none")
            elif row_remaining <= 0:
                row_eta = self.t("eta_zero")
            elif state in ("stopping", "stopped"):
                row_eta = self.t("eta_stopped")
            elif per_rate:
                row_eta = f"{row_remaining / per_rate:.0f} s"
            else:
                row_eta = self.t("eta_wait")
            self._put(row["eta"], row_eta)
            self._put(row["failed"], f"{stored.get('failed') or 0:,}")
            self._put(row["latency"], _fmt_float(stored.get("avg_latency_seconds"), 3))
            self._put(row["tokens"], _fmt_int(stored.get("total_tokens")))

        self._render_card_guards(snapshot)
        self._render_editor_guard(snapshot)

    def _render_config_counts(self, data: dict) -> None:
        progress = data["progress"]
        for name, row in self.config_rows.items():
            stored = progress.get(name)
            if stored is not None:
                self._put(row["count"], str(stored["count"]))

    async def _render_cards(self, data: dict) -> None:
        stamps = data["stamps"]
        changed = [
            name for name in self.cards if name in stamps and stamps[name] != self.stamps.get(name)
        ]
        if not changed:
            return
        payload = await run.io_bound(_load_results, self.database, tuple(changed))
        for name in changed:
            if name not in self.cards:
                continue
            self._render_card(name, payload[name]["rows"], payload[name]["stamp"], refreshed=True)
            self.stamps[name] = payload[name]["stamp"]

    async def _tick(self) -> None:
        self.tick_seq += 1
        seq = self.tick_seq
        card_names = tuple(self.cards)
        config_names = tuple(test["name"] for test in self.project["tests"])
        while True:
            batch = CONTROL.snapshot()
            key = (tuple(batch["names"]), batch["started_at"])
            data = await run.io_bound(
                _load_tick, self.database, key[0], key[1], card_names, config_names
            )
            if data is None:
                return
            # Paint the snapshot observed after the query, never one captured before it.
            snapshot = CONTROL.snapshot()
            if (tuple(snapshot["names"]), snapshot["started_at"]) == key:
                break
        if seq < self.painted_seq:
            return
        self.painted_seq = seq
        self._render_console(snapshot, data)
        self._render_config_counts(data)
        await self._render_cards(data)
        if tuple(data["names"]) != self.result_names:
            self.result_names = tuple(data["names"])
            self.store_names = data["names"]
            self._refresh_result_options()


def main(project: dict) -> None:
    """Register the page and start the NiceGUI server on the configured host/port."""

    @ui.page("/", title="Land or Water", response_timeout=60)
    def index() -> None:
        Panel().build()

    ui.run(
        host=project["ui"]["host"],
        port=project["ui"]["port"],
        reload=False,
        show=False,
        title="Land or Water",
        language=LOCALES.get(project["ui"]["language"], "en-US"),
    )
