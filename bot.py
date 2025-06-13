import os
import json
import re
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

import gspread

# --- Google Sheets: адаптация для Render ---
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
if GOOGLE_CREDS_JSON:
    from oauth2client.service_account import ServiceAccountCredentials
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
else:
    gc = gspread.service_account(filename=os.getenv("GOOGLE_SHEETS_KEY"))

spreadsheet = gc.open("besedka_booking")
huts_sheet = spreadsheet.worksheet("huts")
bookings_sheet = spreadsheet.worksheet("bookings")

# --- FSM (состояния) ---
class BookingState(StatesGroup):
    hut = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    payment = State()

# --- Бот ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = 408892234  # Твой id для чека

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- Кнопки ---
main_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="📅 Забронировать беседку")],
    [KeyboardButton(text="📋 Посмотреть беседки")],
    [KeyboardButton(text="☎️ Позвонить администратору")]
])

# --- Команда /start ---
@dp.message(F.text.lower() == "/start")
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот для аренды беседок 🌿\n\n"
        "Выберите действие 👇",
        reply_markup=main_kb
    )

# --- Просмотр беседок ---
@dp.message(F.text == "📋 Посмотреть беседки")
async def show_huts(message: Message):
    huts = huts_sheet.get_all_records()
    for hut in huts:
        text = (
            f"<b>{hut['Название']}</b>\n"
            f"💰 Цена: {hut['Цена']}₽/час\n"
            f"📄 {hut['Описание']}\n"
        )
        if hut.get('Фото'):
            await message.answer_photo(hut['Фото'], caption=text, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML")

# --- Позвонить администратору ---
@dp.message(F.text == "☎️ Позвонить администратору")
async def call_admin(message: Message):
    # Здесь можно сделать инлайн-кнопку для звонка по номеру
    phone = "+79991234567"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Позвонить 📞", url=f"tel:{phone}")]
        ]
    )
    await message.answer(
        f"Связаться с администратором: <code>{phone}</code>",
        reply_markup=kb,
        parse_mode="HTML"
    )

# --- Начать бронирование ---
@dp.message(F.text == "📅 Забронировать беседку")
async def choose_hut(message: Message, state: FSMContext):
    huts = huts_sheet.get_all_records()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=hut['Название'], callback_data=f"hut_{idx}")]
            for idx, hut in enumerate(huts)
        ]
    )
    await message.answer("Выберите беседку:", reply_markup=kb)
    await state.set_state(BookingState.hut)

# --- Выбор беседки ---
@dp.callback_query(lambda c: c.data and c.data.startswith("hut_"))
async def hut_chosen(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split("_")[1])
    huts = huts_sheet.get_all_records()
    hut = huts[idx]
    await state.update_data(hut=hut)
    await callback.message.answer("Введите дату бронирования (дд.мм.гггг):")
    await state.set_state(BookingState.date)

await callback.answer()

# --- Выбор даты ---
@dp.message(BookingState.date)
async def date_entered(message: Message, state: FSMContext):
    text = message.text.strip()
    if not re.match(r"\d{2}\.\d{2}\.\d{4}", text):
        await message.answer("Пожалуйста, введите дату в формате дд.мм.гггг.")
        return
    await state.update_data(date=text)
    await message.answer("Введите время (например: 12:00–17:00):")
    await state.set_state(BookingState.time)

# --- Проверка и ввод времени ---
@dp.message(BookingState.time)
async def time_entered(message: Message, state: FSMContext):
    time_pattern = r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})"
    m = re.match(time_pattern, message.text.strip().replace('—', '-').replace('–', '-'))
    if not m:
        await message.answer("Введите время в формате: 12:00–17:00")
        return

    time_from, time_to = m.group(1), m.group(2)
    # Проверка минимального времени бронирования
    h_from, m_from = map(int, time_from.split(":"))
    h_to, m_to = map(int, time_to.split(":"))
    total_minutes = (h_to * 60 + m_to) - (h_from * 60 + m_from)
    if total_minutes < 120:
        await message.answer("⏳ Минимальное время бронирования — 2 часа! Попробуйте снова.\nВведите время:")
        return

    # Проверка пересечений с уже забронированными слотами
    data = await state.get_data()
    hut = data["hut"]
    date = data["date"]

    bookings = bookings_sheet.get_all_records()
    for booking in bookings:
        if booking['Беседка'] == hut['Название'] and booking['дата'] == date:
            booked_from = booking['время от'].replace(" ", "")
            booked_to = booking['время до'].replace(" ", "")
            bf_h, bf_m = map(int, booked_from.split(":"))
            bt_h, bt_m = map(int, booked_to.split(":"))
            # Проверка на пересечение по времени
            if not ((h_to * 60 + m_to) <= (bf_h * 60 + bf_m) or (h_from * 60 + m_from) >= (bt_h * 60 + bt_m)):
                busy = f"{booked_from}–{booked_to}"
                await message.answer(
                    f"❗ Эта беседка занята на {date} с {busy}.\nПопробуйте выбрать другое время или дату!"
                )
                return

    await state.update_data(time_from=time_from, time_to=time_to)
    await message.answer("Введите ваше имя:")
    await state.set_state(BookingState.name)

# --- Имя и телефон ---
@dp.message(BookingState.name)
async def name_entered(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📞 Теперь введите номер телефона:")
    await state.set_state(BookingState.phone)

@dp.message(BookingState.phone)
async def phone_entered(message: Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone=phone)
    data = await state.get_data()

    # Итоговая стоимость
    price = int(data["hut"]["Цена"])
    h_from, m_from = map(int, data["time_from"].split(":"))
    h_to, m_to = map(int, data["time_to"].split(":"))
    delta = (h_to * 60 + m_to) - (h_from * 60 + m_from)
    hours = delta // 60
    minutes = delta % 60
    if minutes > 30:
        hours += 1
    elif minutes > 0:
        hours += 0.5
    if hours < 2:
        await message.answer("⏳ Минимальное время бронирования — 2 часа! Попробуйте снова.\nВведите время:")
        await state.set_state(BookingState.time)
        return
    total_cost = int(price * hours)

    # Вставка брони в таблицу
    bookings_sheet.append_row([
        data["hut"]["Название"],
        data["date"],
        data["time_from"],
        data["time_to"],
        data["name"],
        phone,
        "ожидает"
    ])

    pay_info = (
        f"✅ Бронирование отправлено для <b>{data['hut']['Название']}</b> на {data['date']} с {data['time_from']} до {data['time_to']}.\n"
        f"Стоимость аренды: {price}₽/час × {hours} ч = <b>{total_cost}₽</b>\n\n"
        f"🔗 <b>Реквизиты для оплаты</b>: <code>2202202202202202</code> (Сбербанк)\n"
        f"В комментарии укажите: ФИО и дату брони.\n\n"

"Пожалуйста, оплатите в течение 30 минут и отправьте чек в ответ на это сообщение."
    )
    await message.answer(pay_info, parse_mode="HTML")
    await state.set_state(BookingState.payment)

# --- Обработка чека ---
import random
@dp.message(BookingState.payment, F.photo)
async def process_check(message: Message, state: FSMContext):
    data = await state.get_data()
    # Пересылаем чек админу
    name = data.get("name", "гость")
    random_replies = [
        f"Чек получен! Спасибо, {name}, что выбрали нас. Хорошего отдыха, приходите ещё!",
        f"Чек на месте! {name}, приятного отдыха и спасибо за бронирование.",
        f"Отлично, {name}! Оплата принята, ждём вас и желаем отличного отдыха!"
    ]
    await bot.send_photo(
        ADMIN_USER_ID,
        message.photo[-1].file_id,
        caption=(
            f"Новая бронь!\n"
            f"Имя: {name}\n"
            f"Телефон: <code>{data.get('phone','')}</code>\n"
            f"Беседка: {data.get('hut',{}).get('Название','')}\n"
            f"Дата: {data.get('date','')}\n"
            f"Время: {data.get('time_from','')}-{data.get('time_to','')}\n"
        ),
        parse_mode="HTML"
    )
    # Обновляем статус брони в таблице
    bookings = bookings_sheet.get_all_records()
    for idx, row in enumerate(bookings, start=2):
        if (
            row["Беседка"] == data["hut"]["Название"] and
            row["дата"] == data["date"] and
            row["время от"] == data["time_from"] and
            row["время до"] == data["time_to"] and
            row["имя"] == name
        ):
            bookings_sheet.update_cell(idx, 7, "забронировано")
            break
    await message.answer(random.choice(random_replies))
    await state.clear()

# --- Запуск ---
async def main():
    print("✅ Бот запущен и слушает...")
    await dp.start_polling(bot)

if name == "__main__":
    asyncio.run(main())
