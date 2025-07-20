# models.py
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, String, ForeignKey, Boolean
from app.database import Base

class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    title = Column(String, nullable=True)  # Поле для хранения названия чата

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'))
    content = Column(String)
    is_user = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)