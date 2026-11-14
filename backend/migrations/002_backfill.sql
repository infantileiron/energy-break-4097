-- 002_backfill: 批次补传与运行快照
-- datasets.versions 记录每个时刻各字段(kwh/output_kg/status)的来源版本，初始导入为0。
-- runs.snapshot 在运行创建时固定观测、批次名与窗口口径，补传不改已完成结果。
-- 启动时initialize()会对存量库幂等执行同等的加列守卫；本文件供人工审阅与手工执行。
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS versions JSON;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS snapshot JSON;
