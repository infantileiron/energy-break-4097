import threading
from uuid import uuid4
from sqlalchemy import select
from fastapi import HTTPException
from ..models.dataset import MeterDataset
from .readings import parse_csv
from .backfill import FIELDS,Conflict,parse_backfill_csv,merge_records

# 进程内串行化同一批次的补传；跨进程由SELECT FOR UPDATE保证
_locks={}
def _lock(ident):
 return _locks.setdefault(ident,threading.Lock())

def create(db,body):
 if db.scalar(select(MeterDataset).where(MeterDataset.name==body.name)):raise HTTPException(409,"观测批次名称已存在")
 try:readings=parse_csv(body.csv_text)
 except ValueError as e:raise HTTPException(422,str(e))
 versions={r["timestamp"]:{f:0 for f in FIELDS} for r in readings}
 row=MeterDataset(id=str(uuid4()),name=body.name,description=body.description,readings=readings,versions=versions);db.add(row);db.commit();return row

def backfill(db,ident,body):
 with _lock(ident):
  row=db.scalar(select(MeterDataset).where(MeterDataset.id==ident).with_for_update())
  if row is None:raise HTTPException(404,"记录不存在")
  try:records=parse_backfill_csv(body.csv_text)
  except ValueError as e:raise HTTPException(422,str(e))
  try:readings,versions,report=merge_records(row.readings,row.versions,records,body.source_version)
  except Conflict as c:
   db.rollback()
   raise HTTPException(409,{"message":f"同版本v{body.source_version}异值冲突{len(c.conflicts)}处，整批未写入","conflicts":c.conflicts})
  row.readings=readings;row.versions=versions;db.commit()
  return {"dataset_id":row.id,"source_version":body.source_version,**report}
