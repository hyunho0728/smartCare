from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Worker(db.Model):
    __tablename__ = 'WORKER'
    worker_id = db.Column(db.String(50), primary_key=True)
    password = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    org = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    region = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class User(db.Model):
    __tablename__ = 'USER'
    phone_number = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    address = db.Column(db.String(255), nullable=False)
    emergency_contact = db.Column(db.String(20))
    has_underlying_disease = db.Column(db.Boolean, default=False)
    note = db.Column(db.Text)
    worker_phone_number = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class HealthStatus(db.Model):
    __tablename__ = 'HEALTH_STATUS'
    phone_number = db.Column(db.String(20), db.ForeignKey('USER.phone_number', ondelete='CASCADE'), primary_key=True)
    timestamp = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
    condition_level = db.Column(db.Integer, nullable=False)
    meal_status = db.Column(db.Enum('아침', '점심', '저녁', '결식', name='meal_enum'))
    metadata_info = db.Column('metadata', db.Text)

class LoginHistory(db.Model):
    __tablename__ = 'LOGIN_HISTORY'
    phone_number = db.Column(db.String(20), db.ForeignKey('USER.phone_number', ondelete='CASCADE'), primary_key=True)
    auth_time = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
    ip_address = db.Column(db.String(45))