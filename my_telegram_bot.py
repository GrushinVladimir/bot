import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
import os
# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Конфигурация
API_CHANGES_URL = os.getenv('API')  # API для изменений
API_SCHEDULE_URL = os.getenv('API2')  # API для расписания
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("Токен бота не найден. Убедитесь, что переменная окружения BOT_TOKEN установлена.")
    
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Глобальное состояние ресурсов
resources_state = {}
active_chats = set()  # Множество активных чатов, где пользователь запустил бота

# Асинхронная функция для получения данных с API изменений
async def fetch_changes_data():
    try:
        logging.info(f"Запрос к {API_CHANGES_URL}")
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(API_CHANGES_URL)
            response.raise_for_status()
            logging.info("Данные успешно получены.")
            return response.json()
    except Exception as e:
        logging.error(f"Ошибка при запросе к API изменений: {e}")
        return None

# Асинхронная функция для получения данных с API расписания
async def fetch_schedule_data():
    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(API_SCHEDULE_URL)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logging.error(f"Ошибка при запросе к API расписания: {e}")
        return None

# Функция для проверки изменений
async def check_for_updates(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Проверка обновлений...")
    changes_data = await fetch_changes_data()
    schedule_data = await fetch_schedule_data()

    global resources_state, active_chats

    # Проверка изменений в расписании изменений
    if changes_data:
        if "changes" not in resources_state:
            resources_state["changes"] = changes_data  # Инициализация состояния при первом запуске
        else:
            for i, folder in enumerate(changes_data):
                if i >= len(resources_state["changes"]):
                    continue  # Пропустить, если новый раздел добавлен
                for j, resource in enumerate(folder.get('resources', [])):
                    if j >= len(resources_state["changes"][i].get('resources', [])):
                        continue  # Пропустить, если новый ресурс добавлен
                    if resource != resources_state["changes"][i]['resources'][j]:
                        # Обнаружено изменение
                        await notify_users(context, folder, resource, "changes")
            resources_state["changes"] = changes_data  # Обновляем состояние

    # Проверка изменений в основном расписании
    if schedule_data:
        if "schedule" not in resources_state:
            resources_state["schedule"] = schedule_data  # Инициализация состояния при первом запуске
        else:
            for i, resource in enumerate(schedule_data):
                if i >= len(resources_state["schedule"]):
                    continue  # Пропустить, если новый ресурс добавлен
                if resource != resources_state["schedule"][i]:
                    # Обнаружено изменение
                    await notify_users(context, None, resource, "schedule")
            resources_state["schedule"] = schedule_data  # Обновляем состояние

# Функция для отправки уведомлений
async def notify_users(context: ContextTypes.DEFAULT_TYPE, folder, resource, data_type):
    for chat_id in active_chats:
        message = "Внимание, изменение!!!\n"
        if data_type == "changes":
            message += f"Неделя: {folder['pagetitle']}\nФайл: {resource['pagetitle']}"
        elif data_type == "schedule":
            message += f"День: {resource['pagetitle']}"

        # Отправляем текстовое сообщение
        await context.bot.send_message(chat_id=chat_id, text=message)

        # Отправляем PDF-файл
        try:
            async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
                response = await client.get(resource['url'])
                response.raise_for_status()
                pdf_file = response.content
                filename = resource['pagetitle']
                if not filename.endswith('.pdf'):
                    filename += '.pdf'
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=InputFile(pdf_file, filename=filename),
                    caption=f"Файл: {filename}"
                )
        except Exception as e:
            logging.error(f"Ошибка при отправке PDF: {e}")
            await context.bot.send_message(chat_id=chat_id, text="Не удалось отправить PDF.")

# Создание клавиатуры с папками для изменений
def create_changes_folders_keyboard(folders):
    keyboard = []
    for i in range(0, len(folders), 2):  # По 2 кнопки в строке
        row = [
            InlineKeyboardButton(folders[j]['pagetitle'], callback_data=f"changes_folder_{j}")
            for j in range(i, min(i + 2, len(folders)))
        ]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("Обновить ⏳", callback_data="refresh_changes")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(keyboard)

# Создание клавиатуры с ресурсами для изменений
def create_changes_resources_keyboard(resources, folder_index):
    keyboard = [
        [InlineKeyboardButton(resource['pagetitle'], callback_data=f"changes_resource_{folder_index}_{i}")]
        for i, resource in enumerate(resources) if resource.get('url')
    ]
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_changes_folders")])  # Кнопка "Назад"
    return InlineKeyboardMarkup(keyboard)

# Создание клавиатуры с ресурсами для расписания
def create_schedule_keyboard(resources):
    keyboard = []
    for i in range(0, len(resources), 3):  # По 2 кнопки в строке
        row = [
            InlineKeyboardButton(resources[j]['pagetitle'], callback_data=f"schedule_{j}")
            for j in range(i, min(i + 3, len(resources))) if resources[j].get('url')
        ]
        if row:  # Добавляем строку, только если в ней есть кнопки
            keyboard.append(row)
    # Кнопка "Назад" в отдельной строке
    keyboard.append([InlineKeyboardButton("Назад", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(keyboard)

# Отправка PDF-файла
async def send_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, filename: str):
    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(url)
            response.raise_for_status()
            pdf_file = response.content
            if not filename.endswith('.pdf'):
                filename += '.pdf'
            await update.callback_query.message.reply_document(
                document=InputFile(pdf_file, filename=filename)
            )
    except Exception as e:
        logging.error(f"Ошибка при отправке PDF: {e}")
        await update.callback_query.message.reply_text("Не удалось отправить PDF.")

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    active_chats.add(chat_id)  # Добавляем чат в активные
    keyboard = [
        [InlineKeyboardButton("Расписание", callback_data="schedule")],
        [InlineKeyboardButton("Изменения в расписании", callback_data="changes")]
    ]
    await update.message.reply_text(
        "Выберите раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Обработчик callback-запросов
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "changes":
        folders = await fetch_changes_data()
        if folders:
            keyboard = create_changes_folders_keyboard(folders)
            await query.edit_message_text(
                "📅 Выберите неделю:",
                reply_markup=keyboard
            )
        else:
            await query.edit_message_text("Не удалось загрузить данные.")
    elif data == "schedule":
        resources = await fetch_schedule_data()
        if resources:
            keyboard = create_schedule_keyboard(resources)
            await query.edit_message_text(
                "📚 Выберите группу:",
                reply_markup=keyboard
            )
        else:
            await query.edit_message_text("Не удалось загрузить данные.")
    elif data.startswith("changes_folder_"):
        folder_index = int(data.split("_")[2])
        folders = await fetch_changes_data()
        if folders and 0 <= folder_index < len(folders):
            resources = folders[folder_index].get('resources', [])
            keyboard = create_changes_resources_keyboard(resources, folder_index)
            await query.edit_message_text(
                f"📚 Неделя: {folders[folder_index]['pagetitle']}",
                reply_markup=keyboard
            )
    elif data.startswith("changes_resource_"):
        parts = data.split("_")
        if len(parts) == 4:
            folder_index = int(parts[2])
            resource_index = int(parts[3])
            folders = await fetch_changes_data()
            if folders and 0 <= folder_index < len(folders):
                resources = folders[folder_index].get('resources', [])
                if 0 <= resource_index < len(resources):
                    resource = resources[resource_index]
                    await send_pdf(update, context, resource['url'], resource['pagetitle'])
    elif data.startswith("schedule_"):
        resource_index = int(data.split("_")[1])
        resources = await fetch_schedule_data()
        if resources and 0 <= resource_index < len(resources):
            resource = resources[resource_index]
            await send_pdf(update, context, resource['url'], resource['pagetitle'])
    elif data == "back_to_changes_folders":
        folders = await fetch_changes_data()
        if folders:
            keyboard = create_changes_folders_keyboard(folders)
            await query.edit_message_text(
                "📅 Выберите неделю:",
                reply_markup=keyboard
            )
    elif data == "back_to_main_menu":
        keyboard = [
            [InlineKeyboardButton("Расписание", callback_data="schedule")],
            [InlineKeyboardButton("Изменения в расписании", callback_data="changes")]
        ]
        await query.edit_message_text(
            "Выберите раздел:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data == "refresh_changes":
        folders = await fetch_changes_data()
        if folders:
            keyboard = create_changes_folders_keyboard(folders)
            await query.edit_message_text(
                "📅 Выберите неделю:",
                reply_markup=keyboard
            )

# Запуск бота
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Настройка периодической задачи для проверки обновлений каждые 15 секунд
    job_queue = application.job_queue
    job_queue.run_repeating(check_for_updates, interval=300.0, first=5.0)  # 15 секунд

    application.run_polling()

if __name__ == "__main__":
    main()
