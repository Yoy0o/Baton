---
name: tms
description: 排班系统的领域知识——班次编码与时间、排班名单的用途。
---

- 班次编码：early=早班（06:00–14:00），late=晚班（14:00–22:00），时间为东八区。
- “明天早班的司机”指 date=明天、shift=early 的排班名单，不等于当前在岗司机。
- 名单不含位置；需要位置时把 result_id 交给 fleet__get_driver_positions。
