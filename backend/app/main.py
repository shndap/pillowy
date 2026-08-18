from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import hmac, json
from pathlib import Path
from zoneinfo import ZoneInfo
import asyncio
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from aiogram import Bot
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

class Settings(BaseSettings):
    telegram_bot_token: str = ""
    database_url: str = f"sqlite:///{(Path(__file__).resolve().parents[2] / 'pill_tracker.db').as_posix()}"
    scheduler_secret: str = "change-me"
    frontend_url: str = "http://localhost:5173"
    environment: str = "development"
    demo_mode: bool = False
    class Config: env_file = ".env"

settings = Settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(120), default="Friend")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    inventory: Mapped[int] = mapped_column(Integer, default=0)
    schedules: Mapped[list["Schedule"]] = relationship(cascade="all, delete-orphan")
class Schedule(Base):
    __tablename__ = "medication_schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"), index=True)
    period: Mapped[str] = mapped_column(String(32), default="Morning")
    at: Mapped[time] = mapped_column()
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
class Intake(Base):
    __tablename__ = "intakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"))
    schedule_id: Mapped[int] = mapped_column(ForeignKey("medication_schedules.id"))
    taken_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "schedule_id", "taken_on", name="uq_intake_schedule_day"),)
class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    schedule_id: Mapped[int] = mapped_column(ForeignKey("medication_schedules.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime)
    notification_type: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "schedule_id", "scheduled_for", "notification_type", name="uq_notification"),)

Base.metadata.create_all(engine)
app = FastAPI(title="Pill Cabinet API")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url, "http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()
def verify_init_data(raw: str) -> dict:
    if not raw and (settings.environment == "development" or settings.demo_mode): return {"id": "demo", "first_name": "Alex"}
    if not raw: raise HTTPException(401, "Telegram initialization data is required")
    pairs = dict(parse_qsl(raw, keep_blank_values=True)); received = pairs.pop("hash", "")
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), sha256).digest()
    check = hmac.new(secret, "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs)).encode(), sha256).hexdigest()
    if not hmac.compare_digest(check, received): raise HTTPException(401, "Invalid Telegram signature")
    user = json.loads(pairs.get("user", "{}")); return user
def current_user(telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"), session: Session = Depends(db)) -> User:
    info = verify_init_data(telegram_init_data or ""); tid = info.get("id")
    if tid == "demo": tid = 10001
    user = session.scalar(select(User).where(User.telegram_id == int(tid)))
    if not user:
        user = User(telegram_id=int(tid), first_name=info.get("first_name", "Friend")); session.add(user); session.commit(); session.refresh(user)
    return user

class MedicationIn(BaseModel): name: str = Field(min_length=1, max_length=120); description: Optional[str] = None; inventory: int = Field(0, ge=0)
class ScheduleIn(BaseModel): period: str = "Morning"; at: time; quantity: int = Field(1, ge=1); reminder_enabled: bool = True
class MedicationPatch(BaseModel): name: Optional[str] = Field(None, min_length=1, max_length=120); description: Optional[str] = None; inventory: Optional[int] = Field(None, ge=0); active: Optional[bool] = None
class SchedulePatch(BaseModel): period: Optional[str] = None; at: Optional[time] = None; quantity: Optional[int] = Field(None, ge=1); reminder_enabled: Optional[bool] = None; enabled: Optional[bool] = None
class PurchaseIn(BaseModel): quantity: int = Field(gt=0)
class IntakeIn(BaseModel): schedule_id: int

@app.get("/health")
def health(): return {"ok": True}
@app.get("/me")
def me(user: User = Depends(current_user)): return {"first_name": user.first_name, "timezone": user.timezone}
@app.get("/medications")
def medications(user: User = Depends(current_user), session: Session = Depends(db)):
    meds = session.scalars(select(Medication).where(Medication.user_id == user.id, Medication.active == True)).all()
    return [{"id":m.id,"name":m.name,"description":m.description,"inventory":m.inventory,"schedules":[{"id":s.id,"period":s.period,"at":s.at.strftime("%H:%M"),"quantity":s.quantity,"reminder_enabled":s.reminder_enabled} for s in m.schedules if s.enabled]} for m in meds]
@app.post("/medications")
def add_medication(body: MedicationIn, user: User = Depends(current_user), session: Session = Depends(db)):
    med = Medication(user_id=user.id, name=body.name, description=body.description, inventory=body.inventory); session.add(med); session.commit(); return {"id":med.id}
@app.patch("/medications/{medication_id}")
def edit_medication(medication_id: int, body: MedicationPatch, user: User = Depends(current_user), session: Session = Depends(db)):
    med = session.scalar(select(Medication).where(Medication.id == medication_id, Medication.user_id == user.id))
    if not med: raise HTTPException(404, "Medication not found")
    for key, value in body.model_dump(exclude_unset=True).items(): setattr(med, key, value)
    session.commit(); return {"updated": True}
@app.delete("/medications/{medication_id}")
def delete_medication(medication_id: int, user: User = Depends(current_user), session: Session = Depends(db)):
    med = session.scalar(select(Medication).where(Medication.id == medication_id, Medication.user_id == user.id))
    if not med: raise HTTPException(404, "Medication not found")
    med.active = False
    for schedule in med.schedules: schedule.enabled = False
    session.commit(); return {"deleted": True}
@app.post("/medications/{medication_id}/schedules")
def add_schedule(medication_id: int, body: ScheduleIn, user: User = Depends(current_user), session: Session = Depends(db)):
    med = session.scalar(select(Medication).where(Medication.id==medication_id, Medication.user_id==user.id))
    if not med: raise HTTPException(404, "Medication not found")
    schedule = Schedule(medication_id=med.id, **body.model_dump()); session.add(schedule); session.commit(); return {"id":schedule.id}
@app.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, user: User = Depends(current_user), session: Session = Depends(db)):
    schedule = session.scalar(select(Schedule).join(Medication).where(Schedule.id == schedule_id, Medication.user_id == user.id))
    # Deletion is intentionally idempotent: a retry after a successful delete
    # should still leave the client in the desired state.
    if not schedule: return {"deleted": False}
    session.delete(schedule); session.commit(); return {"deleted": True}
@app.patch("/schedules/{schedule_id}")
def edit_schedule(schedule_id: int, body: SchedulePatch, user: User = Depends(current_user), session: Session = Depends(db)):
    schedule = session.scalar(select(Schedule).join(Medication).where(Schedule.id == schedule_id, Medication.user_id == user.id))
    if not schedule: raise HTTPException(404, "Schedule not found")
    for key, value in body.model_dump(exclude_unset=True).items(): setattr(schedule, key, value)
    session.commit(); return {"updated": True}
@app.post("/inventory/{medication_id}/purchase")
def purchase(medication_id: int, body: PurchaseIn, user: User = Depends(current_user), session: Session = Depends(db)):
    med = session.scalar(select(Medication).where(Medication.id==medication_id, Medication.user_id==user.id))
    if not med: raise HTTPException(404, "Medication not found")
    med.inventory += body.quantity; session.add(InventoryTransaction(medication_id=med.id, quantity=body.quantity, transaction_type="PURCHASE")); session.commit(); return {"inventory":med.inventory}
def record_schedule(session: Session, user: User, schedule: Schedule, day: date):
    if session.scalar(select(Intake).where(Intake.user_id==user.id, Intake.schedule_id==schedule.id, Intake.taken_on==day)): return False
    med = session.get(Medication, schedule.medication_id)
    if med.inventory < schedule.quantity: raise HTTPException(409, f"Not enough {med.name} pills")
    med.inventory -= schedule.quantity; session.add(Intake(user_id=user.id, medication_id=med.id, schedule_id=schedule.id, taken_on=day, quantity=schedule.quantity)); session.add(InventoryTransaction(medication_id=med.id, quantity=-schedule.quantity, transaction_type="INTAKE")); return True
@app.post("/schedules/{schedule_id}/take")
def take(schedule_id: int, user: User = Depends(current_user), session: Session = Depends(db)):
    schedule = session.scalar(select(Schedule).join(Medication).where(Schedule.id==schedule_id, Medication.user_id==user.id));
    if not schedule: raise HTTPException(404, "Schedule not found")
    changed = record_schedule(session,user,schedule,date.today()); session.commit(); return {"recorded":changed}
@app.post("/today/take-all")
def take_all(period: str = "Morning", user: User = Depends(current_user), session: Session = Depends(db)):
    schedules = session.scalars(select(Schedule).join(Medication).where(Medication.user_id==user.id, Schedule.period==period, Schedule.enabled==True)).all(); count=0
    for schedule in schedules: count += int(record_schedule(session,user,schedule,date.today()))
    session.commit(); return {"recorded":count,"message":f"{period} pills recorded"}
@app.get("/today")
def today(user: User = Depends(current_user), session: Session = Depends(db)):
    schedules=session.scalars(select(Schedule).join(Medication).where(Medication.user_id==user.id,Schedule.enabled==True)).all(); day=date.today(); groups={}
    for s in schedules:
        med=session.get(Medication,s.medication_id); taken=session.scalar(select(Intake).where(Intake.schedule_id==s.id,Intake.taken_on==day)); groups.setdefault(s.period,[]).append({"schedule_id":s.id,"medication_id":med.id,"name":med.name,"quantity":s.quantity,"taken":bool(taken),"at":s.at.strftime("%H:%M")})
    return {"date":day.isoformat(),"groups":groups}
@app.get("/history")
def history(user: User = Depends(current_user), session: Session = Depends(db)):
    rows=session.scalars(select(Intake).where(Intake.user_id==user.id).order_by(Intake.taken_on.desc()).limit(100)).all(); return [{"date":r.taken_on.isoformat(),"medication_id":r.medication_id,"quantity":r.quantity} for r in rows]
@app.post("/internal/scheduler/tick")
def scheduler_tick(x_scheduler_secret: Optional[str]=Header(None), session: Session=Depends(db)):
    if not hmac.compare_digest(x_scheduler_secret or "", settings.scheduler_secret): raise HTTPException(403,"Forbidden")
    now = datetime.now(timezone.utc).replace(tzinfo=None); cutoff = now - timedelta(minutes=10); processed = 0
    # The unique database key makes this safe when an external cron retries a tick.
    for user in session.scalars(select(User)).all():
        try: local_now = datetime.now(ZoneInfo(user.timezone))
        except Exception: local_now = datetime.now(timezone.utc)
        local_day = local_now.date()
        for schedule in session.scalars(select(Schedule).join(Medication).where(Medication.user_id==user.id, Schedule.enabled==True, Schedule.reminder_enabled==True)).all():
            scheduled_local = datetime.combine(local_day, schedule.at, tzinfo=local_now.tzinfo)
            scheduled_utc = scheduled_local.astimezone(timezone.utc).replace(tzinfo=None)
            if cutoff <= scheduled_utc <= now:
                exists = session.scalar(select(NotificationLog).where(NotificationLog.user_id==user.id, NotificationLog.schedule_id==schedule.id, NotificationLog.scheduled_for==scheduled_utc, NotificationLog.notification_type=="DOSE"))
                if not exists:
                    med = session.get(Medication, schedule.medication_id)
                    if settings.telegram_bot_token:
                        text = f"💊 {schedule.period} pills\n\n{med.name} ×{schedule.quantity}\n\nOpen your pill cabinet to record this dose."
                        try:
                            asyncio.run(Bot(settings.telegram_bot_token).send_message(user.telegram_id, text))
                        except Exception:
                            continue
                    session.add(NotificationLog(user_id=user.id, schedule_id=schedule.id, scheduled_for=scheduled_utc, notification_type="DOSE")); processed += 1
    session.commit()
    return {"processed":processed,"status":"ok","window_minutes":10}
