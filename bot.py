import os
import json
import datetime
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
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
import gspread

# --- Google Sheets: Render или локально ---
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
    time_from = State()
    time_to = State()
    name = State()
    phone = State()
    payment = State()

# --- Бот ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = 408892234

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

main_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="📅 Забронировать беседку")],
    [KeyboardButton(text="📋 Посмотреть беседки")],
    [KeyboardButton(text="☎️ Позвонить администратору")]
])

# --- /start ---
@dp.message(F.text.lower() == "/start")
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! Я бот для аренды беседок 🌿\n\nВыберите действие 👇", reply_markup=main_kb)

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
    phone = "+79991234567"
    await message.answer(
        f"Связаться с администратором: <code>{phone}</code>",
        parse_mode="HTML"
    )

# --- Начать бронирование: выбрать беседку ---
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

@dp.callback_query(lambda c: c.data and c.data.startswith("hut_"))
async def hut_chosen(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split("_")[1])
    huts = huts_sheet.get_all_records()
    hut = huts[idx]
    await state.update_data(hut=hut)
    await callback.message.answer(
        "Выберите дату бронирования:",
        reply_markup=await SimpleCalendar(min_date=datetime.date.today()).start_calendar()
    )
    await callback.answer()
    await state.set_state(BookingState.date)

# --- Выбор даты (через календарь) ---
@dp.callback_query(SimpleCalendarCallback.filter())
async def process_date(callback_query: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    selected_date = callback_data.selected_date
    await state.update_data(date=selected_date.strftime("%d.%m.%Y"))
    await callback_query.message.answer(
        f"✅ Дата выбрана: {selected_date.strftime('%d.%m.%Y')}\nТеперь выберите время начала аренды:",
        reply_markup=make_time_keyboard()
    )
    await state.set_state(BookingState.time_from)
    await callback_query.answer()

# --- Клавиатура времени (30 мин шаг) ---
def make_time_keyboard():
    times = []
    h = 8
    m = 0
    while h < 21 or (h == 21 and m == 0):
        times.append(f"{h:02d}:{m:02d}")
        m += 30
        if m == 60:
            m = 0
            h += 1
    # 4 кнопки в строке
    keyboard = []
    row = []
    for idx, t in enumerate(times, 1):
        row.append(InlineKeyboardButton(text=t, callback_data=f"timefrom_{t}"))
        if idx % 4 == 0 or t == times[-1]:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def make_time_to_keyboard(selected_from):
    # Предлагаем окончания только после selected_from + 2 часа, не позже 21:00
    h_from, m_from = map(int, selected_from.split(":"))
    from_minutes = h_from * 60 + m_from + 120  # минимум 2 часа
    end_times = []
    h = 8
    m = 0
    while h < 21 or (h == 21 and m == 0):
        minutes = h * 60 + m
        if minutes > from_minutes:
            end_times.append(f"{h:02d}:{m:02d}")
        m += 30
        if m == 60:
            m = 0
            h += 1
    # 4 кнопки в строке
    keyboard = []
    row = []
    for idx, t in enumerate(end_times, 1):
        row.append(InlineKeyboardButton(text=t, callback_data=f"timeto_{t}"))
        if idx % 4 == 0 or t == end_times[-1]:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Выбор времени ОТ ---
@dp.callback_query(lambda c: c.data and c.data.startswith("timefrom_"))
async def time_from_chosen(callback: CallbackQuery, state: FSMContext):
    t_from = callback.data.replace("timefrom_", "")
    await state.update_data(time_from=t_from)
    await callback.message.answer("Теперь выберите время окончания аренды:", reply_markup=make_time_to_keyboard(t_from))
    await state.set_state(BookingState.time_to)
    await callback.answer()

# --- Выбор времени ДО ---
@dp.callback_query(lambda c: c.data and c.data.startswith("timeto_"))
async def time_to_chosen(callback: CallbackQuery, state: FSMContext):
    t_to = callback.data.replace("timeto_", "")
    data = await state.get_data()
    t_from = data["time_from"]
    # Проверка: чтобы интервал не пересекался с бронями и не был меньше 2ч
    h_from, m_from = map(int, t_from.split(":"))
    h_to, m_to = map(int, t_to.split(":"))
    delta = (h_to * 60 + m_to) - (h_from * 60 + m_from)
    if delta < 120:
        await callback.message.answer("⏳ Минимальное время бронирования — 2 часа! Выберите другое время.")
        return

    # Проверка на пересечения с бронями
    bookings = bookings_sheet.get_all_records()
    hut = data["hut"]["Название"]
    date = data["date"]
    for booking in bookings:
        if booking['Беседка'] == hut and booking['дата'] == date:
            bf_h, bf_m = map(int, booking['время от'].split(":"))
            bt_h, bt_m = map(int, booking['время до'].split(":"))
            booked_from = bf_h * 60 + bf_m
            booked_to = bt_h * 60 + bt_m
            # Пересечение
            if not (h_to * 60 + m_to <= booked_from or h_from * 60 + m_from >= booked_to):
                busy = f"{booking['время от']}-{booking['время до']}"
                await callback.message.answer(
                    f"❗ Эта беседка уже занята на {date} с {busy}.\nПопробуйте выбрать другое время или дату!"
                )
                return
    await state.update_data(time_to=t_to)
    await callback.message.answer("Введите ваше имя:")
    await state.set_state(BookingState.name)
    await callback.answer()

# --- Имя, телефон, подтверждение, оплата, чек ---

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
    price = int(data["hut"]["Цена"])
    h_from, m_from = map(int, data["time_from"].split(":"))
    h_to, m_to = map(int, data["time_to"].split(":"))
    delta = (h_to * 60 + m_to) - (h_from * 60 + m_from)
    hours = delta // 60
    if delta % 60 != 0:
        hours += 0.5
    total_cost = int(price * hours)

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

import random
@dp.message(BookingState.payment, F.photo)
async def process_check(message: Message, state: FSMContext):
    data = await state.get_data()
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
    # Обновляем статус
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

if __name__ == "__main__":
    asyncio.run(main())
