from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import httpx
import os

from starlette.responses import RedirectResponse, FileResponse

from .database import get_db, engine, Base
from .models import Chat, Message

# Загрузка переменных окружения

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Настройка статических файлов и шаблонов
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

MISTRAL_API_KEY = "VymMEJoUAHo7r8zs1aVy6LDoxsZ06oyq"
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"


@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("app/templates/chat.html")

@app.post("/chats/create")
async def create_chat(db: Session = Depends(get_db)):
    try:
        chat = Chat()
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return {
            "status": "success",
            "chat_id": chat.id,
            "created_at": chat.created_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/chat/{chat_id}")
@app.get("/chat/{chat_id}")
async def get_chat_data(chat_id: int, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.created_at.asc()).all()

    return {
        "chat_id": chat.id,
        "title": chat.title if chat.title else f"Chat {chat.id}",  # Всегда возвращаем название или Chat ID
        "messages": [
            {
                "id": msg.id,
                "content": msg.content,
                "is_user": msg.is_user,
                "created_at": msg.created_at.isoformat()
            }
            for msg in messages
        ]
    }


@app.get("/chat/{chat_id}", response_class=HTMLResponse)
async def get_chat(chat_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        chat = db.query(Chat).filter(Chat.id == chat_id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")

        # Убедимся, что сообщения сортируются по времени создания
        messages = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.created_at.asc()).all()
        chats = db.query(Chat).all()

        return templates.TemplateResponse("chat.html", {
            "request": request,
            "current_chat": chat,
            "messages": messages,
            "chats": chats
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/chat/{chat_id}/send")
async def send_message(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    data = await request.json()
    message = data.get("message")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # Проверяем, первое ли это сообщение в чате
    is_first_message = not db.query(Message).filter(Message.chat_id == chat_id).count()

    # Сохраняем сообщение пользователя
    user_message = Message(
        chat_id=chat_id,
        content=message,
        is_user=True
    )
    db.add(user_message)
    db.commit()

    # Если это первое сообщение, генерируем название чата
    chat_title = None
    if is_first_message:
        title = await generate_chat_title(message)
        chat = db.query(Chat).filter(Chat.id == chat_id).first()
        if chat:
            chat.title = title
            db.commit()
            chat_title = title

    # Получаем все сообщения из этого чата
    messages = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.created_at).all()

    # Формируем историю сообщений для Mistral API
    chat_history = [{
        "role": "user" if msg.is_user else "assistant",
        "content": msg.content
    } for msg in messages]

    # Отправляем запрос к Mistral API
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                MISTRAL_API_URL,
                json={
                    "model": "mistral-tiny",
                    "messages": chat_history,
                    "temperature": 0.7
                },
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            assistant_content = data['choices'][0]['message']['content']

            # Сохраняем ответ Mistral
            assistant_message = Message(
                chat_id=chat_id,
                content=assistant_content,
                is_user=False
            )
            db.add(assistant_message)
            db.commit()

            return {
                "status": "success",
                "message": assistant_content,
                "message_id": assistant_message.id,
                "created_at": assistant_message.created_at.isoformat(),
                "is_first_message": is_first_message,
                "chat_title": chat_title  # Возвращаем сгенерированное название
            }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

async def generate_chat_title(first_message: str) -> str:
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = (
        "Создайте краткий заголовок 3-5 слов для чата на основе этого первого сообщения: "
        f"'{first_message}'. Вернуть только заголовок, ничего больше. Заголовок должен быть на русском"
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                MISTRAL_API_URL,
                json={
                    "model": "mistral-tiny",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                },
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            title = data['choices'][0]['message']['content'].strip('"').strip()
            # Убедимся, что title не пустой и не содержит только кавычки
            return title if title and title.strip() != '""' else f"Chat about {first_message[:20]}..."
    except Exception as e:
        print(f"Error generating chat title: {e}")
        return f"Chat about {first_message[:20]}..."  # Fallback title


@app.post("/create_chat/")
async def create_chat(db: Session = Depends(get_db)):
    try:
        # Проверяем, есть ли незавершенные транзакции
        if db.in_transaction():
            db.rollback()

        chat = Chat()
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return {"status": "success", "chat_id": chat.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": str(e)}
        )

@app.get("/chats")
async def get_chats(db: Session = Depends(get_db)):
    chats = db.query(Chat).order_by(Chat.created_at.desc()).all()
    return [
        {
            "id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at.isoformat()
        }
        for chat in chats
    ]


