from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# 1. 사회복지사 모델
class Worker(db.Model):
    __tablename__ = 'SOCIAL_WORKER'
    worker_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(100))
    org = db.Column(db.String(100))
    address = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


# 2. 어르신(사용자) 모델
class User(db.Model):
    __tablename__ = 'USER'
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    worker_id = db.Column(db.Integer, db.ForeignKey('SOCIAL_WORKER.worker_id', ondelete='SET NULL'))
    name = db.Column(db.String(50), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    address = db.Column(db.String(255), nullable=False)
    emergency_contact = db.Column(db.String(20))
    has_underlying_disease = db.Column(db.Boolean, default=False)
    underlying_disease_severity = db.Column(db.Integer, default=0)
    note = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


# 3. 건강 상태 모델
class HealthStatus(db.Model):
    __tablename__ = 'HEALTH_STATUS'
    status_id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('USER.user_id', ondelete='CASCADE'), nullable=False)
    condition_level = db.Column(db.SmallInteger, nullable=False)
    breakfast_status = db.Column(db.Enum('완료', '예정', '결식'), default='완료', nullable=False)
    lunch_status = db.Column(db.Enum('완료', '예정', '결식'), default='완료', nullable=False)
    dinner_status = db.Column(db.Enum('완료', '예정', '결식'), default='완료', nullable=False)
    blood_pressure = db.Column(db.String(20))
    blood_sugar = db.Column(db.Integer)
    target_date = db.Column(db.Date, default=datetime.now().date, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    note = db.Column(db.Text)


# 4. 접속 이력 모델
class LoginHistory(db.Model):
    __tablename__ = 'LOGIN_HISTORY'
    history_id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('USER.user_id', ondelete='CASCADE'))
    phone_number = db.Column(db.String(20), nullable=False)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    auth_time = db.Column(db.DateTime, default=datetime.now, nullable=False)