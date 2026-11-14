from sqlalchemy import create_engine,inspect,text
from sqlalchemy.orm import DeclarativeBase,sessionmaker
from .settings import DATABASE_URL
class Base(DeclarativeBase): pass
engine=create_engine(DATABASE_URL,pool_pre_ping=True)
Session=sessionmaker(bind=engine,expire_on_commit=False)
def get_db():
 with Session() as db: yield db
def initialize():
 from .. import models
 Base.metadata.create_all(engine)
 # 存量库的加列守卫（仅新增可空列，与migrations/002_backfill.sql一致，幂等）
 with engine.begin() as conn:
  for table,column in [("datasets","versions"),("runs","snapshot")]:
   if column not in [c["name"] for c in inspect(conn).get_columns(table)]:
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} JSON"))
