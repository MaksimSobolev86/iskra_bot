import asyncio
import os
import calendar
from datetime import datetime, date as dt_date, time as dt_time
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import gspread
import re
import random

# --- Настройки ---
ADMIN_ID = 408892234
ADMIN_PHONE = "+79991234567"
WORK_START = 8
WORK_END = 21

# --- Инициализация ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GS_KEY = os.getenv("GOOGLE_SHEETS_KEY")

gc = gspread.service_account(filename=GS_KEY)
spreadsheet = gc.open("besedka_booking")
huts_sheet = spreadsheet.worksheet("huts")
bookings_sheet = spreadsheet.worksheet("bookings")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- FSM ---
class Booking(StatesGroup):
    choosing_hut = State()
    choosing_date = State()
    choosing_time_from = State()
    choosing_time_to = State()
    entering_name = State()
    entering_phone = State()
    waiting_receipt = State()

# --- Утилиты ---
def build_calendar(year=None, month=None):
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    markup = {"inline_keyboard": []}
    month_name = calendar.month_name[month]
    markup["inline_keyboard"].append([
        {"text": f'{month_name} {year}', "callback_data": 'ignore'}
    ])
    week_days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    markup["inline_keyboard"].append([
        {"text": day, "callback_data": 'ignore'} for day in week_days
    ])
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append({"text": " ", "callback_data": "ignore"})
            else:
                d = dt_date(year, month, day)
                today = dt_date.today()
                disabled = d < today
                row.append({
                    "text": str(day) if not disabled else "❌",
                    "callback_data": f"select_date:{year}:{month}:{day}" if not disabled else "ignore"
                })
        markup["inline_keyboard"].append(row)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    markup["inline_keyboard"].append([
        {"text": "⬅️", "callback_data": f"nav:{prev_year}:{prev_month}"},
        {"text": "➡️", "callback_data": f"nav:{next_year}:{next_month}"}
    ])
    return InlineKeyboardMarkup(**markup)

def get_time_buttons(start=8, end=21, step_min=30):
    builder = InlineKeyboardBuilder()
    times = []
    for h in range(start, end):
        for m in (0, 30):
            t = f"{h:02}:{m:02d}"
            times.append(t)
    for i in range(0, len(times), 4):
        row = times[i:i+4]
        builder.row(*[InlineKeyboardButton(text=x, callback_data=f"time:{x}") for x in row])
    return builder.as_markup()

def validate_time_range(time_from, time_to):
    t_from = datetime.strptime(time_from, "%H:%M")
    t_to = datetime.strptime(time_to, "%H:%M")
    delta = (t_to - t_from).total_seconds() / 60
    return delta >= 120 and t_from < t_to

def is_date_past(date_str):
    try:
        day, month, year = map(int, date_str.split("."))
        chosen = dt_date(year, month, day)
        return chosen < dt_date.today()
    except Exception:
        return True

def is_slot_busy(hut_name, date, time_from, time_to):
    bookings = bookings_sheet.get_all_records()
    user_from = datetime.strptime(time_from, "%H:%M").time()
    user_to = datetime.strptime(time_to, "%H:%M").time()
    for booking in bookings:
        if str(booking.get("Беседка")).strip().lower() == hut_name.strip().lower() and booking.get("дата") == date:
            booked_from = booking.get("время от")
            booked_to = booking.get("время до")
            try:
                booked_from_t = datetime.strptime(booked_from, "%H:%M").time()
                booked_to_t = datetime.strptime(booked_to, "%H:%M").time()
            except Exception:
                continue
            if not (user_to <= booked_from_t or user_from >= booked_to_t):
                return True
    return False

def get_busy_times(hut_name, date):
    bookings = bookings_sheet.get_all_records()
    times = []
    for booking in bookings:
        if (
            str(booking.get("Беседка")).strip().lower() == hut_name.strip().lower()
            and booking.get("дата") == date
        ):
            from_ = booking.get("время от", "")
            to_ = booking.get("время до", "")
            if from_ and to_:
                times.append(f"{from_} – {to_}")
    return times

# --- Хендлеры ---
main_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="📅 Забронировать беседку")],
    [KeyboardButton(text="📋 Посмотреть беседки")],
    [KeyboardButton(text="📞 Позвонить администратору")]
])

@dp.message(F.text.lower() == "/start")
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот для аренды беседок 🌿\n\nВыберите действие 👇",
        reply_markup=main_kb
    )

@dp.message(F.text == "📞 Позвонить администратору")
async def call_admin(message: Message):
    await message.answer(
        f"Связаться с администратором:\n<code>{ADMIN_PHONE}</code>",
        parse_mode="HTML"
    )

@dp.message(F.text == "📋 Посмотреть беседки")
async def show_huts(message: Message):
    huts = huts_sheet.get_all_records()
    if not huts:
        await message.answer("Нет доступных беседок.")
        return
    for hut in huts:
        text = (
            f"<b>{hut['Беседка']}</b>\n"
            f"💰 {hut['Цена']}₽/час\n"
            f"📝 {hut.get('Описание', '')}"
        )
        if hut.get('Фото'):
            await message.answer_photo(hut['Фото'], caption=text, parse_mode="HTML")
        else:
            await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📅 Забронировать беседку")
async def choose_hut(message: Message, state: FSMContext):
    huts = huts_sheet.get_all_records()
    if not huts:
        await message.answer("Нет свободных беседок.")
        return
    builder = InlineKeyboardBuilder()
    for i, hut in enumerate(huts):
        builder.button(text=hut['Беседка'], callback_data=f"choose_hut:{i}")
    await message.answer("Выберите беседку:", reply_markup=builder.as_markup())
    await state.set_state(Booking.choosing_hut)

@dp.callback_query(F.data.startswith("choose_hut:"))
async def hut_selected(callback: CallbackQuery, state: FSMContext):
    huts = huts_sheet.get_all_records()
    idx = int(callback.data.split(":")[1])
    hut = huts[idx]
    await state.update_data(hut_name=hut['Беседка'], hut_price=hut['Цена'])
    await callback.message.edit_text(
        f"Вы выбрали: {hut['Беседка']}\nТеперь выберите дату:",
        reply_markup=build_calendar()
    )
    await state.set_state(Booking.choosing_date)
    await callback.answer()

@dp.callback_query(F.data.startswith("nav:"))
async def nav_calendar(callback: CallbackQuery, state: FSMContext):
    _, year, month = callback.data.split(":")
    markup = build_calendar(int(year), int(month))
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("select_date:"))
async def date_selected(callback: CallbackQuery, state: FSMContext):
    _, year, month, day = callback.data.split(":")
    date_str = f"{int(day):02}.{int(month):02}.{year}"
    if is_date_past(date_str):
        await callback.answer("❌ Нельзя бронировать на прошедшую дату.", show_alert=True)
        return
    await state.update_data(date=date_str)
    data = await state.get_data()
    hut_name = data['hut_name']
    busy_times = get_busy_times(hut_name, date_str)
    text = f"✅ Дата выбрана: {date_str}\n\n"
    if busy_times:
        text += "<b>⏳ Уже занято:</b>\n" + "\n".join(f"• {x}" for x in busy_times) + "\n\n"
    else:
        text += "<b>Все окна свободны на этот день!</b>\n\n"
    text += "Выберите время начала:"
    await callback.message.edit_text(
        text, reply_markup=get_time_buttons()
    )
    await state.set_state(Booking.choosing_time_from)
    await callback.answer()

@dp.callback_query(F.data.startswith("time:"))
async def select_time_from(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("time_from"):
        # выбор времени начала
        time_from = callback.data.split(":")[1]
        await state.update_data(time_from=time_from)
        await callback.message.edit_text(
            f"Время начала: <b>{time_from}</b>\nТеперь выберите время окончания:",
            reply_markup=get_time_buttons(start=int(time_from[:2])+1)
        )
        await state.set_state(Booking.choosing_time_to)
    else:
        # выбор времени окончания
        time_to = callback.data.split(":")[1]
        time_from = data["time_from"]
        if not validate_time_range(time_from, time_to):
            await callback.answer("⏰ Минимум 2 часа и время окончания позже начала!", show_alert=True)
            return
        if is_slot_busy(data["hut_name"], data["date"], time_from, time_to):
            busy_times = get_busy_times(data["hut_name"], data["date"])
            await callback.message.edit_text(
                "❌ Эта беседка уже занята на выбранную дату и время.\n\n"
                + "<b>⏳ Уже занято:</b>\n"
                + "\n".join(f"• {x}" for x in busy_times)
                + "\n\nПопробуйте выбрать другое время!",
                parse_mode="HTML"
            )
            await state.clear()
            return
        await state.update_data(time_to=time_to)
        await callback.message.edit_text("Введите ваше имя:")
        await state.set_state(Booking.entering_name)
    await callback.answer()

@dp.message(Booking.entering_name)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📞 Теперь введите номер телефона:")
    await state.set_state(Booking.entering_phone)

@dp.message(Booking.entering_phone)
async def get_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    data = await state.get_data()
    price = int(data["hut_price"])
    t1 = datetime.strptime(data["time_from"], "%H:%M")
    t2 = datetime.strptime(data["time_to"], "%H:%M")
    total_hours = int((t2 - t1).total_seconds() // 3600)
    total_cost = price * total_hours
    row = [
        data["hut_name"],
        data["date"],
        data["time_from"],
        data["time_to"],
        data["name"],
        data["phone"],
        "ожидает"
    ]
    bookings_sheet.append_row(row)
    await message.answer(
        f"✅ Бронирование отправлено для <b>{data['hut_name']}</b> на {data['date']} "
        f"с {data['time_from']} до {data['time_to']}.\n"
        f"<b>Стоимость аренды: {price}₽/час × {total_hours} ч = <u>{total_cost}₽</u></b>\n\n"
        "🔗 Реквизиты для оплаты:\n<code>2202202202202202</code> (Сбербанк)\n"
        "В комментарии укажите: ФИО и дату брони.\n\n"
        "Пожалуйста, оплатите в течение 30 минут и отправьте чек в ответ на это сообщение.",
        parse_mode="HTML"
    )
    await state.set_state(Booking.waiting_receipt)

@dp.message(Booking.waiting_receipt, F.photo)
@dp.message(Booking.waiting_receipt, F.document)
async def receive_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    caption = (
        f"Чек по бронированию:\n"
        f"Беседка: {data.get('hut_name')}\n"
        f"Дата: {data.get('date')}\n"
        f"Время: {data.get('time_from')} - {data.get('time_to')}\n"
        f"Имя: {data.get('name')}\n"
        f"Телефон: <code>{data.get('phone')}</code>"
    )
    if message.photo:
        await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=caption, parse_mode="HTML")
    elif message.document:
        await bot.send_document(ADMIN_ID, document=message.document.file_id, caption=caption, parse_mode="HTML")

    all_bookings = bookings_sheet.get_all_values()
    header = all_bookings[0]
    for idx, row in enumerate(all_bookings[1:], start=2):
        if (
            row[header.index("Беседка")].strip() == data["hut_name"].strip()
            and row[header.index("дата")].strip() == data["date"].strip()
            and row[header.index("время от")].strip() == data["time_from"].strip()
            and row[header.index("время до")].strip() == data["time_to"].strip()
            and row[header.index("статус брони")].strip() == "ожидает"
        ):
            bookings_sheet.update_cell(idx, header.index("статус брони")+1, "забронировано")
            break

    name = data.get('name', 'гость')
    replies = [
        f"✅ Чек получен! Спасибо, {name}, что выбрали нас. Хорошего отдыха! Приходите ещё 🌿",
        f"✅ Спасибо, {name}, платёж получен! Отличного отдыха и ждём вас снова 🤗",
        f"✅ Ваш чек успешно получен, {name}! Наслаждайтесь отдыхом — будем рады видеть ещё!"
    ]
    reply_text = random.choice(replies)
    await message.answer(reply_text)
    await state.clear()

async def main():
    print("✅ Бот запущен и слушает...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
