import csv,io,math
from .readings import parse_time

FIELDS=("kwh","output_kg","status")

class Conflict(Exception):
 def __init__(self,conflicts):
  super().__init__(f"同版本异值冲突{len(conflicts)}处")
  self.conflicts=conflicts

def parse_backfill_csv(text):
 rows=list(csv.DictReader(io.StringIO(text)))
 if not rows or len(rows)>5000:raise ValueError("CSV需1至5000行")
 header=rows[0].keys()
 if "timestamp" not in header:raise ValueError("CSV需含timestamp列")
 columns=[c for c in FIELDS if c in header]
 if not columns:raise ValueError("至少包含kwh,output_kg,status中的一列；空单元格表示不提供的保留，null表示显式缺测")
 result=[];seen=set()
 for index,row in enumerate(rows,2):
  timestamp=parse_time(row.get("timestamp")).isoformat()
  if timestamp in seen:raise ValueError(f"第{index}行时间重复（已统一UTC）")
  seen.add(timestamp)
  provided={}
  for key in columns:
   raw=row.get(key)
   if raw is None or raw.strip()=="":continue
   raw=raw.strip()
   if raw.lower()=="null":
    if key=="status":raise ValueError(f"第{index}行status不可为null，应为running/stopped")
    provided[key]=None;continue
   if key=="status":
    if raw not in ["running","stopped"]:raise ValueError(f"第{index}行状态应为running/stopped")
    provided[key]=raw;continue
   try:value=float(raw)
   except Exception:raise ValueError(f"第{index}行{key}必须为数值、空值或null")
   if not math.isfinite(value) or value<0:raise ValueError(f"第{index}行{key}必须非负且有限")
   provided[key]=value
  result.append({"timestamp":timestamp,"provided":provided})
 return result

def merge_records(readings,versions,records,source_version):
 """字段级合并：高版本覆盖，低版本与同版本同值忽略，同版本异值整批冲突。
 返回(新观测,新版本表,报告)；冲突时抛Conflict，传入数据不被改动。"""
 versions=versions or {}
 new_readings=[dict(r) for r in readings]
 index={r["timestamp"]:r for r in new_readings}
 new_versions={ts:dict(fv) for ts,fv in versions.items()}
 details=[];conflicts=[];applied=ignored=added=0
 for rec in records:
  timestamp=rec["timestamp"];provided=rec["provided"]
  row=index.get(timestamp)
  if row is None:
   added+=1;applied+=len(provided)
   row={"timestamp":timestamp,"kwh":None,"output_kg":None,"status":"running"}
   fv={f:0 for f in FIELDS}
   for field,value in provided.items():row[field]=value;fv[field]=source_version
   new_readings.append(row);index[timestamp]=row;new_versions[timestamp]=fv
   details.append({"timestamp":timestamp,"field":"*","outcome":"added","reason":f"新增时刻，写入{len(provided)}项；未提供项记缺测，状态默认running"})
   continue
  fv=new_versions.setdefault(timestamp,{f:0 for f in FIELDS})
  if not provided:
   ignored+=1;details.append({"timestamp":timestamp,"field":"*","outcome":"ignored","reason":"本行未提供字段，保留原值"})
   continue
  for field,value in provided.items():
   current=fv.get(field,0)
   if source_version>current:
    row[field]=value;fv[field]=source_version;applied+=1
    details.append({"timestamp":timestamp,"field":field,"outcome":"applied","reason":f"v{source_version}覆盖v{current}"})
   elif source_version<current:
    ignored+=1
    details.append({"timestamp":timestamp,"field":field,"outcome":"ignored","reason":f"已有更高版本v{current}，忽略v{source_version}"})
   elif row[field]==value:
    ignored+=1
    details.append({"timestamp":timestamp,"field":field,"outcome":"ignored","reason":"同版本同值，重复补传不改动"})
   else:
    conflicts.append({"timestamp":timestamp,"field":field,"version":source_version,"existing":row[field],"incoming":value})
 if conflicts:raise Conflict(conflicts)
 new_readings.sort(key=lambda r:r["timestamp"])
 report={"received":len(records),"applied":applied,"ignored":ignored,"added":added,"readings_count":len(new_readings),"details":details}
 return new_readings,new_versions,report
