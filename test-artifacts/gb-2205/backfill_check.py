# -*- coding: utf-8 -*-
"""批次补传验收：跨时区重复、交叉补传、版本合并、冲突回滚、并发、运行快照固定。
运行：python3 test-artifacts/gb-2205/backfill_check.py（SQLite，无需外部服务）"""
import os,sys,json,threading
DB="/tmp/gb2205-backfill.db"
if os.path.exists(DB):os.remove(DB)
os.environ["DATABASE_URL"]=f"sqlite:///{DB}"
os.environ["SEED_DEMO"]="false"
os.environ["JWT_SECRET"]="gb2205-test-secret"
sys.path.insert(0,"/workspace/backend")
import jwt
from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Session
from src.models.user import User
from src.models.dataset import MeterDataset
from src.services import run_service,dataset_service
from src.schemas.dataset import DatasetBackfill

run_service.enqueue=lambda run_id:None  # 验收不依赖Redis

failures=[]
def check(name,cond,info=""):
 print(("PASS " if cond else "FAIL ")+name+(f" | {info}" if info else ""))
 if not cond:failures.append(name)

with TestClient(app) as client:
 with Session() as db:
  for role in ["admin","analyst","viewer"]:
   db.add(User(id="u-"+role,email=role+"@t",role=role,password_hash="x"))
  db.commit()
 def token(role):return {"Authorization":"Bearer "+jwt.encode({"sub":"u-"+role},"gb2205-test-secret",algorithm="HS256")}
 AH=token("analyst");VH=token("viewer")

 def create_dataset(name,csv_text):
  r=client.post("/datasets",headers=AH,json={"name":name,"csv_text":csv_text,"description":""})
  assert r.status_code==201,(name,r.text)
  return r.json()["id"]
 def backfill(ident,version,csv_text,headers=None):
  return client.post(f"/datasets/{ident}/backfill",headers=AH if headers is None else headers,json={"source_version":version,"csv_text":csv_text})
 def readings(ident):
  return {r["timestamp"]:r for r in client.get(f"/datasets/{ident}",headers=VH).json()["readings"]}

 # A. 初始导入：版本表初始化为0
 did=create_dataset("批次A","timestamp,kwh,output_kg,status\n2026-10-01T00:00:00+00:00,10,100,running\n2026-10-01T01:00:00+00:00,20,,running\n")
 with Session() as db:
  row=db.get(MeterDataset,did)
  check("A1 初始导入版本表为0",row.versions["2026-10-01T00:00:00+00:00"]=={"kwh":0,"output_kg":0,"status":0})
 check("A2 空单元格入库记缺测",readings(did)["2026-10-01T01:00:00+00:00"]["output_kg"] is None)

 # B. 交叉补传：电量与产量分次补传，互不丢失（整行覆盖会丢另一项的核心场景）
 r=backfill(did,1,"timestamp,output_kg\n2026-10-01T01:00:00+00:00,50\n")
 check("B1 补传产量成功",r.status_code==200 and r.json()["applied"]==1,r.text)
 h1=readings(did)["2026-10-01T01:00:00+00:00"]
 check("B2 补产量后电量保留",h1["kwh"]==20 and h1["output_kg"]==50,json.dumps(h1))
 r=backfill(did,2,"timestamp,kwh\n2026-10-01T01:00:00+00:00,25\n")
 h1=readings(did)["2026-10-01T01:00:00+00:00"]
 check("B3 再补电量后产量保留",h1["kwh"]==25 and h1["output_kg"]==50,json.dumps(h1))

 # C. 跨时区：+08:00与UTC同一时刻合并到同一条；批内等价时刻重复拒绝
 r=backfill(did,3,"timestamp,kwh\n2026-10-01T08:00:00+08:00,99\n")
 rows=readings(did)
 check("C1 跨时区时刻归并不新增行",r.status_code==200 and len(rows)==2 and rows["2026-10-01T00:00:00+00:00"]["kwh"]==99,r.text)
 r=backfill(did,4,"timestamp,kwh\n2026-10-01T08:00:00+08:00,1\n2026-10-01T00:00:00Z,2\n")
 check("C2 批内跨时区重复422",r.status_code==422 and "重复" in r.json()["detail"],r.text)

 # D. 旧版本不改结果
 r=backfill(did,1,"timestamp,kwh\n2026-10-01T00:00:00+00:00,1\n")
 d=r.json()["details"][0]
 check("D1 旧版本忽略",r.status_code==200 and readings(did)["2026-10-01T00:00:00+00:00"]["kwh"]==99 and d["outcome"]=="ignored" and "更高版本" in d["reason"],r.text)

 # E. 重复补传幂等：同版本同值不改结果，可原样重试
 before=json.dumps(readings(did),sort_keys=True)
 r=backfill(did,3,"timestamp,kwh\n2026-10-01T08:00:00+08:00,99\n")
 check("E1 重复补传同版本同值忽略",r.status_code==200 and r.json()["applied"]==0 and "同版本同值" in r.json()["details"][0]["reason"],r.text)
 check("E2 重复补传后数据不变",json.dumps(readings(did),sort_keys=True)==before)

 # F. 显式null记缺测；空单元格保留
 r=backfill(did,4,"timestamp,kwh,output_kg\n2026-10-01T00:00:00+00:00,null,\n")
 h0=readings(did)["2026-10-01T00:00:00+00:00"]
 check("F1 显式null记缺测",r.status_code==200 and h0["kwh"] is None,r.text)
 check("F2 空单元格保留原值",h0["output_kg"]==100,json.dumps(h0))

 # G. 同版本异值整批拒绝且不留下部分更新
 before=json.dumps(readings(did),sort_keys=True)
 r=backfill(did,4,"timestamp,kwh\n2026-10-01T00:00:00+00:00,123\n2026-10-03T00:00:00+00:00,5\n")
 check("G1 同版本异值409",r.status_code==409,r.text)
 detail=r.json()["detail"]
 check("G2 冲突原因含现有值与补传值",detail["conflicts"][0]["existing"] is None and detail["conflicts"][0]["incoming"]==123 and detail["conflicts"][0]["field"]=="kwh",json.dumps(detail,ensure_ascii=False))
 check("G3 冲突后整批回滚无部分更新",json.dumps(readings(did),sort_keys=True)==before)
 r=backfill(did,4,"timestamp,kwh\n2026-10-01T00:00:00+00:00,123\n2026-10-03T00:00:00+00:00,5\n")
 check("G4 失败后可原样重试仍409",r.status_code==409 and json.dumps(readings(did),sort_keys=True)==before)

 # H. 提高版本后整批生效；再次重试幂等
 r=backfill(did,5,"timestamp,kwh\n2026-10-01T00:00:00+00:00,123\n2026-10-03T00:00:00+00:00,5\n")
 rows=readings(did)
 check("H1 高版本覆盖并新增时刻",r.status_code==200 and rows["2026-10-01T00:00:00+00:00"]["kwh"]==123 and rows["2026-10-03T00:00:00+00:00"]["kwh"]==5,r.text)
 check("H2 新增时刻未提供项记缺测、状态默认running",rows["2026-10-03T00:00:00+00:00"]["output_kg"] is None and rows["2026-10-03T00:00:00+00:00"]["status"]=="running")
 before=json.dumps(rows,sort_keys=True)
 r=backfill(did,5,"timestamp,kwh\n2026-10-01T00:00:00+00:00,123\n2026-10-03T00:00:00+00:00,5\n")
 check("H3 成功补传原样重试幂等",r.status_code==200 and r.json()["applied"]==0 and json.dumps(readings(did),sort_keys=True)==before,r.text)

 # I. 状态字段参与版本合并
 r=backfill(did,6,"timestamp,status\n2026-10-03T00:00:00+00:00,stopped\n")
 check("I1 状态可补传覆盖",r.status_code==200 and readings(did)["2026-10-03T00:00:00+00:00"]["status"]=="stopped",r.text)
 r=backfill(did,7,"timestamp,status\n2026-10-03T00:00:00+00:00,null\n")
 check("I2 状态不可为null",r.status_code==422,r.text)

 # J. 并发补传：不同字段各自保留最新值；同字段高版本获胜
 def worker(version,csv_text):
  with Session() as db:
   dataset_service.backfill(db,did,DatasetBackfill(source_version=version,csv_text=csv_text))
 errors=[]
 def race(pairs):
  barrier=threading.Barrier(len(pairs)+1)
  def threaded(version,csv_text):
   try:barrier.wait();worker(version,csv_text)
   except Exception as e:errors.append(str(e))
  ts=[threading.Thread(target=threaded,args=p) for p in pairs]
  for t in ts:t.start()
  barrier.wait()
  for t in ts:t.join()
 race([(7,"timestamp,kwh\n2026-10-01T01:00:00+00:00,777\n"),(7,"timestamp,output_kg\n2026-10-01T01:00:00+00:00,888\n")])
 h1=readings(did)["2026-10-01T01:00:00+00:00"]
 check("J1 并发交叉补传各项都保留",not errors and h1["kwh"]==777 and h1["output_kg"]==888,json.dumps(h1)+str(errors))
 race([(9,"timestamp,kwh\n2026-10-01T01:00:00+00:00,900\n"),(8,"timestamp,kwh\n2026-10-01T01:00:00+00:00,800\n")])
 check("J2 并发同字段高版本获胜",not errors and readings(did)["2026-10-01T01:00:00+00:00"]["kwh"]==900,str(errors))

 # K. 权限：未登录401，只读403
 check("K1 未登录401",backfill(did,1,"timestamp,kwh\n2026-10-01T00:00:00+00:00,1\n",headers={}).status_code==401)
 check("K2 只读403",backfill(did,1,"timestamp,kwh\n2026-10-01T00:00:00+00:00,1\n",headers=VH).status_code==403)
 check("K3 版本下限校验",backfill(did,0,"timestamp,kwh\n2026-10-01T00:00:00+00:00,1\n").status_code==422)

 # L. 运行固定创建时的观测版本，补传不改已完成结果
 csv_rows="timestamp,kwh,output_kg,status\n"+"".join(f"2026-11-0{1+i//24}T{i%24:02d}:00:00+00:00,{250+i},100,running\n" for i in range(30))
 did2=create_dataset("批次B",csv_rows)
 wid=client.post("/windows",headers=AH,json={"name":"窗口B","start_at":"2026-11-01T00:00:00+00:00","end_at":"2026-11-02T23:00:00+00:00","note":""}).json()["id"]
 sid=client.post("/studies",headers=AH,json={"name":"研究B","dataset_id":did2,"window_id":wid,"penalty":6,"min_segment":3,"anomaly_threshold":3.5,"noise_floor":0.02}).json()["id"]
 run_id=client.post("/runs",headers=AH,json={"study_id":sid}).json()["id"]
 backfill(did2,1,"timestamp,kwh\n"+"".join(f"2026-11-0{1+i//24}T{i%24:02d}:00:00+00:00,999\n" for i in range(30)))
 with Session() as db:run_service.compute(db,run_id)
 result=client.get(f"/runs/{run_id}",headers=VH).json()["result"]
 check("L1 运行使用创建时快照而非补传后数据",result["valid_count"]==30 and result["observations"][0]["kwh"]==250,json.dumps(result["observations"][0]))
 snapshot_result=json.dumps(result,sort_keys=True)
 backfill(did2,2,"timestamp,kwh\n2026-11-01T00:00:00+00:00,555\n")
 result2=client.get(f"/runs/{run_id}",headers=VH).json()["result"]
 check("L2 补传不改已完成结果",json.dumps(result2,sort_keys=True)==snapshot_result)
 run_id2=client.post("/runs",headers=AH,json={"study_id":sid}).json()["id"]
 with Session() as db:run_service.compute(db,run_id2)
 result3=client.get(f"/runs/{run_id2}",headers=VH).json()["result"]
 check("L3 新运行看到补传后数据",result3["observations"][0]["kwh"]==555,json.dumps(result3["observations"][0]))

print()
if failures:print(f"{len(failures)}项失败："+"、".join(failures));sys.exit(1)
print("全部通过")
