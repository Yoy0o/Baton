---
name: fleet
description: 车队追踪地图台的领域知识——区域编码、司机与挂车数据、地图界面工具的用法与坑点。
---

术语：
- 区域编码：east-china=华东（上海周边），north-china=华北（北京周边）。
- 司机 ID 形如 D0012；挂车 ID 形如 T-2031。
- 停留：挂车在同一地点连续不动的时段，duration_min 为停留分钟数。

常用套路：
- 司机分组：排班名单（tms）→ fleet__get_driver_positions 取位置 → analytics__run_grouping → 展示。
- 地图展示：用户开着车队地图时用 fleet__show_result_layer(result_id, color_by)；它只改界面，不改数据。
- 轨迹回放、定位司机是地图界面自带功能：fleet__play_track、fleet__focus_driver。

坑点：
- 位置是最近一次上报，可能延迟几分钟到几十分钟。
- 地图同时叠加多个临时图层时会显得杂乱，必要时先 fleet__clear_result_layers。
