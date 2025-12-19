import asyncio
import os
import sqlite3
from typing import List, Optional

import discord
from discord import app_commands
from discord.ui import Button, Modal, TextInput, View
from rcon.source import Client

# ========== КОНФИГУРАЦИЯ ==========
# ЗАМЕНИТЕ ЭТИ ЗНАЧЕНИЯ НА СВОИ!
TOKEN = os.getenv("DISCORD_TOKEN")
RCON_HOST = "karmalis.ru"  # Пример: "123.123.123.123"
RCON_PORT = 25794  # Стандартный порт RCON
RCON_PASSWORD = os.getenv("RCON_PASSWORD")  # Пароль из server.properties
WHITELIST_ROLE_ID = 1446108377766690816  # ID роли для вайтлиста в Discord
LEADER_ROLE_ID = 1450529742712471723
APPLICATIONS_CHANNEL_ID = 1446140359901057198  # Канал для заявок
ANNOUNCEMENT_CHANNEL_ID = 1446108086258634773  # Канал для кнопки регистрации

# ========== НАСТРОЙКА БОТА ==========
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


class Database:
    def __init__(self):
        self.conn = sqlite3.connect("karmator.db", check_same_thread=False)
        self.cursor = self.conn.cursor()

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            regId INTEGER PRIMARY KEY AUTOINCREMENT,
            discordId INTEGER NOT NULL UNIQUE,
            mcNickname TEXT,
            country TEXT,
            isLeader BOOLEAN
        )""")
        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            countryId INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            citizenRoleId INTEGER NOT NULL UNIQUE,
            karma INTEGER DEFAULT 0
        )""")
        self.conn.commit()

    def register_player(self, discord_id, mc_nickname, country):
        try:
            # Нормализуем название страны (приводим к нижнему регистру для поиска)
            country_normalized = country.strip().lower()

            # Ищем страну в БД (регистронезависимо)
            self.cursor.execute(
                "SELECT name, citizenRoleId FROM countries WHERE LOWER(name) = ?",
                (country_normalized,),
            )
            country_data = self.cursor.fetchone()

            if not country_data:
                # Страна не найдена
                return {"success": False, "error": "country_not_found"}

            actual_country_name, citizen_role_id = country_data

            # Проверяем, не зарегистрирован ли уже пользователь
            if self.check_player(discord_id):
                return {"success": False, "error": "already_registered"}

            # Регистрируем игрока
            self.cursor.execute(
                """
            INSERT INTO players (discordId, mcNickname, country, isLeader)
            VALUES (?, ?, ?, ?)
            """,
                (discord_id, mc_nickname, actual_country_name, False),
            )
            self.conn.commit()

            return {
                "success": True,
                "citizen_role_id": citizen_role_id,
                "country": actual_country_name,
            }
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return {"success": False, "error": "already_registered"}
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def register_player_without_country_check(
        self, discord_id, mc_nickname, country_name
    ):
        """Регистрирует игрока, даже если страны нет в БД. Возвращает True при успехе."""
        try:
            # Проверяем, не зарегистрирован ли уже пользователь
            if self.check_player(discord_id):
                return {"success": False, "error": "already_registered"}

            # Регистрируем игрока с указанной страной (даже если её нет в таблице countries)
            self.cursor.execute(
                """
                INSERT INTO players (discordId, mcNickname, country, isLeader)
                VALUES (?, ?, ?, ?)
                """,
                (discord_id, mc_nickname, country_name, False),
            )
            self.conn.commit()
            return {"success": True, "country": country_name}
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                return {"success": False, "error": "already_registered"}
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_player(self, discord_id):
        self.cursor.execute(
            "SELECT * FROM players WHERE discordId=?",
            (discord_id,),
        )
        return self.cursor.fetchone() is not None

    def get_player(self, discord_id):
        self.cursor.execute(
            "SELECT * FROM players WHERE discordId=?",
            (discord_id,),
        )
        return self.cursor.fetchone()

    def toggle_player_leader(self, discord_id):
        self.cursor.execute(
            "SELECT isLeader, country FROM players WHERE discordId = ?", (discord_id,)
        )
        result = self.cursor.fetchone()
        if result:
            is_leader, country = result
            self.cursor.execute(
                "UPDATE players SET isLeader = ? WHERE discordId = ?",
                (not bool(is_leader), discord_id),
            )
            self.conn.commit()
            return {
                "success": True,
                "old_status": bool(is_leader),
                "new_status": not bool(is_leader),
                "country": country,
            }
        return {"success": False}

    def change_player_nickname(self, discord_id, new_nickname):
        try:
            self.cursor.execute(
                "UPDATE players SET mcNickname = ? WHERE discordId = ?",
                (new_nickname, discord_id),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def create_country(self, country_name: str, citizen_role_id: int) -> bool:
        try:
            self.cursor.execute(
                "INSERT INTO countries (name, citizenRoleId) VALUES (?, ?)",
                (country_name, citizen_role_id),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_country_by_role(self, citizen_role_id: int):
        self.cursor.execute(
            "SELECT * FROM countries WHERE citizenRoleId = ?", (citizen_role_id,)
        )
        return self.cursor.fetchone()

    def get_country_by_name(self, country_name: str):
        self.cursor.execute(
            "SELECT * FROM countries WHERE LOWER(name) = ?", (country_name.lower(),)
        )
        return self.cursor.fetchone()

    def get_all_countries(self) -> List[tuple]:
        self.cursor.execute("SELECT * FROM countries ORDER BY karma DESC")
        return self.cursor.fetchall()

    def modify_karma_value(self, country_name: str, quantity: int) -> bool:
        try:
            # Находим страну (регистронезависимо)
            self.cursor.execute(
                "SELECT name FROM countries WHERE LOWER(name) = ?",
                (country_name.lower(),),
            )
            country = self.cursor.fetchone()

            if not country:
                return False

            actual_country_name = country[0]

            self.cursor.execute(
                "UPDATE countries SET karma = karma + ? WHERE name = ?",
                (quantity, actual_country_name),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def get_country_karma(self, country_name: str) -> Optional[int]:
        try:
            self.cursor.execute(
                "SELECT karma FROM countries WHERE LOWER(name) = ?",
                (country_name.lower(),),
            )
            result = self.cursor.fetchone()
            return result[0] if result else None
        except Exception:
            return None

    def get_country_stats(self):
        """Получает статистику всех стран"""
        self.cursor.execute("""
            SELECT c.name, c.karma, COUNT(p.discordId) as citizens_count
            FROM countries c
            LEFT JOIN players p ON c.name = p.country
            GROUP BY c.name
            ORDER BY c.karma DESC
        """)
        return self.cursor.fetchall()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.close()


# Вместо этого используйте этот простой код:
async def execute_rcon_command(command: str) -> str:
    """Самая простая рабочая версия"""
    try:
        # Прямой синхронный вызов в потоке
        def run_command():
            with Client(
                RCON_HOST, RCON_PORT, passwd=RCON_PASSWORD, timeout=5.0
            ) as client:
                return client.run(command)

        result = await asyncio.to_thread(run_command)
        return str(result).strip()
    except Exception as e:
        return f"Ошибка: {type(e).__name__}: {str(e)}"


# ========== МОДАЛЬНОЕ ОКНО АНКЕТЫ ==========
class UserFormModal(Modal, title="📝 Анкета для вайтлиста"):
    minecraft_username = TextInput(
        label="Твой ник в Minecraft",
        placeholder="Steve123",
        required=True,
        max_length=25,
    )

    country = TextInput(
        label="В какой стране планируешь играть?",
        placeholder="Лефринтия / Создам свою",
        required=True,
        max_length=50,
    )

    rules = TextInput(
        label="Ознакомился с правилами сервера?",
        placeholder="Да, ознакомился и согласен",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Проверяем, не зарегистрирован ли уже игрок
        if database.check_player(interaction.user.id):
            await interaction.response.send_message(
                "❌ Вы уже зарегистрированы! Вы не можете подать заявку повторно.",
                ephemeral=True,
            )
            return

        # Отправляем благодарность игроку
        await interaction.response.send_message(
            "✅ Спасибо! Твоя анкета отправлена на рассмотрение. "
            "Ожидай ответа в личных сообщениях.",
            ephemeral=True,
        )

        # Создаем View с кнопками для админов
        admin_view = AdminView()
        admin_view.applicant = interaction.user
        admin_view.applicant_data = {
            "minecraft": self.minecraft_username.value,
            "country": self.country.value,
            "rules": self.rules.value,
        }

        # Отправляем заявку в канал модерации
        channel = bot.get_channel(APPLICATIONS_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="🆕 Новая заявка на вайтлист",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="👤 Игрок",
                value=f"{interaction.user.mention}\n`{interaction.user}`",
                inline=False,
            )
            embed.add_field(
                name="🎮 Ник в Minecraft",
                value=f"`{self.minecraft_username.value}`",
                inline=True,
            )
            embed.add_field(name="🌍 Страна", value=self.country.value, inline=True)
            embed.add_field(name="✅ Правила", value=self.rules.value, inline=False)
            embed.set_footer(text=f"ID: {interaction.user.id}")

            await channel.send(embed=embed, view=admin_view)


# ========== КНОПКА ДЛЯ ОТКРЫТИЯ АНКЕТЫ ==========
class RegistrationButton(Button):
    def __init__(self):
        self.database = database
        super().__init__(
            label="Подать заявку", style=discord.ButtonStyle.primary, emoji="✍️"
        )

    async def callback(self, interaction: discord.Interaction):
        if self.database.check_player(interaction.user.id):
            await interaction.response.send_message(
                "❌ Ты уже зарегистрирован! Вы не можете подать заявку повторно.",
                ephemeral=True,
            )
        else:
            modal = UserFormModal()
            await interaction.response.send_modal(modal)


# ========== VIEW С КНОПКОЙ РЕГИСТРАЦИИ ==========
class RegistrationView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RegistrationButton())


# ========== КНОПКИ АДМИНИСТРАТОРА ==========
class AdminView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.applicant = None  # Будет установлен при создании заявки
        self.applicant_data = None
        self.message = None  # Для хранения сообщения с заявкой

        # Добавляем кнопки
        self.add_item(AcceptButton())
        self.add_item(DeclineButton())
        self.add_item(BanButton())


# ========== КЛАСС AcceptButton (ИСПРАВЛЕННЫЙ) ==========
class AcceptButton(Button):
    def __init__(self):
        self.database = database
        super().__init__(
            label="Принять",
            style=discord.ButtonStyle.success,
            custom_id="accept_btn",
            emoji="✅",
        )

    async def callback(self, interaction: discord.Interaction):
        view: AdminView = self.view

        # Проверка прав
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ У вас недостаточно прав!", ephemeral=True
            )
            return

        # Проверяем, не зарегистрирован ли уже игрок
        if self.database.check_player(view.applicant.id):
            await interaction.response.send_message(
                "❌ Этот игрок уже зарегистрирован!", ephemeral=True
            )
            return

        # Откладываем ответ, т.к. операции могут занять время
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 1. ПОИСК УЧАСТНИКА ГАРАНТИРОВАННО
            guild = interaction.guild

            # Способ 1: fetch_member (рекомендуется)
            try:
                member = await guild.fetch_member(view.applicant.id)
            except discord.NotFound:
                # Если пользователь покинул сервер
                await interaction.followup.send(
                    f"❌ Пользователь {view.applicant} не найден на сервере!",
                    ephemeral=True,
                )
                return

            # 2. ПОИСК РОЛИ ВАЙТЛИСТА
            whitelist_role = guild.get_role(WHITELIST_ROLE_ID)
            if not whitelist_role:
                await interaction.followup.send(
                    f"❌ Роль с ID {WHITELIST_ROLE_ID} не найдена!", ephemeral=True
                )
                return

            # 3. РЕГИСТРАЦИЯ ИГРОКА В БД
            mc_username = view.applicant_data["minecraft"]
            country_name = view.applicant_data["country"]

            # Сначала пробуем стандартную регистрацию (если страна существует)
            result_db_member_adding = self.database.register_player(
                member.id, mc_username, country_name
            )

            if not result_db_member_adding["success"]:
                error_msg = result_db_member_adding.get("error", "unknown_error")
                if error_msg == "already_registered":
                    await interaction.followup.send(
                        "❌ Этот игрок уже зарегистрирован в базе данных!",
                        ephemeral=True,
                    )
                    return
                elif error_msg == "country_not_found":
                    # Страна не найдена, регистрируем без проверки страны
                    result_db_member_adding = (
                        self.database.register_player_without_country_check(
                            member.id, mc_username, country_name
                        )
                    )

                    if not result_db_member_adding["success"]:
                        error_msg = result_db_member_adding.get(
                            "error", "unknown_error"
                        )
                        if error_msg == "already_registered":
                            await interaction.followup.send(
                                "❌ Этот игрок уже зарегистрирован в базе данных!",
                                ephemeral=True,
                            )
                        else:
                            await interaction.followup.send(
                                f"❌ Ошибка регистрации в БД: {error_msg}",
                                ephemeral=True,
                            )
                        return

                    # Игрок зарегистрирован, но страны нет в БД
                    actual_country_name = result_db_member_adding["country"]
                    citizen_role = None
                    role_status_citizen = f"⚠️ Роль гражданина НЕ ВЫДАНА. Страна '{actual_country_name}' не найдена в системе. Создайте страну через /createcountry и выдайте роль вручную."
                else:
                    await interaction.followup.send(
                        f"❌ Ошибка регистрации в БД: {error_msg}", ephemeral=True
                    )
                    return
            else:
                # Стандартный сценарий: страна найдена
                citizen_role_id = result_db_member_adding["citizen_role_id"]
                actual_country_name = result_db_member_adding["country"]
                citizen_role = guild.get_role(citizen_role_id)

                if not citizen_role:
                    role_status_citizen = f"⚠️ Роль гражданина (ID: {citizen_role_id}) не найдена. Пожалуйста, выдайте роль вручную."
                else:
                    try:
                        await member.add_roles(
                            citizen_role, reason="Регистрация гражданина"
                        )
                        role_status_citizen = (
                            f"✅ Роль гражданина '{citizen_role.name}' выдана"
                        )
                    except discord.Forbidden:
                        role_status_citizen = "❌ Нет прав для выдачи роли гражданина"
                    except discord.HTTPException as e:
                        role_status_citizen = f"❌ Ошибка выдачи роли гражданина: {e}"

            # 4. ВЫДАЧА РОЛИ ВАЙТЛИСТА
            try:
                await member.add_roles(whitelist_role, reason="Вайтлист одобрен")
                role_status_whitelist = (
                    f"✅ Роль вайтлиста '{whitelist_role.name}' выдана"
                )
            except discord.Forbidden:
                role_status_whitelist = "❌ Нет прав для выдачи роли вайтлиста"
                await interaction.followup.send(
                    "❌ У бота нет прав 'Управлять ролями' или его роль слишком низкая!",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as e:
                role_status_whitelist = f"❌ Ошибка выдачи роли вайтлиста: {e}"
                await interaction.followup.send(
                    f"❌ Ошибка выдачи роли: {e}", ephemeral=True
                )
                return

            # 5. RCON КОМАНДА
            rcon_response = await execute_rcon_command(f"easywl add {mc_username}")

            # 6. УВЕДОМЛЕНИЕ ИГРОКА В ЛС
            dm_sent = False
            try:
                embed = discord.Embed(
                    title="🎉 Заявка одобрена!",
                    description="Добро пожаловать на сервер!",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Сервер", value=guild.name)
                embed.add_field(name="Ваш ник в Minecraft", value=mc_username)
                embed.add_field(name="Ваша страна", value=actual_country_name)
                embed.add_field(name="Администратор", value=interaction.user.mention)
                embed.add_field(name="Роль вайтлиста", value=whitelist_role.mention)
                if citizen_role:
                    embed.add_field(name="Роль гражданина", value=citizen_role.mention)

                await view.applicant.send(embed=embed)
                dm_sent = True
            except discord.Forbidden:
                dm_sent = False

            # 7. ОБНОВЛЕНИЕ СООБЩЕНИЯ С ЗАЯВКОЙ
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.title = f"✅ ЗАЯВКА ОДОБРЕНА ({interaction.user.name})"
            embed.add_field(
                name="Роль вайтлиста", value=whitelist_role.mention, inline=False
            )
            embed.add_field(
                name="Роль гражданина", value=role_status_citizen, inline=False
            )
            embed.add_field(name="Страна", value=actual_country_name, inline=False)
            embed.add_field(
                name="RCON команда", value=f"`easywl add {mc_username}`", inline=False
            )
            embed.add_field(
                name="Ответ сервера", value=f"```{rcon_response}```", inline=False
            )

            await interaction.message.edit(embed=embed, view=None)

            # 8. ФИНАЛЬНЫЙ ОТВЕТ АДМИНУ
            message_lines = [
                f"**✅ Заявка обработана!**",
                f"👤 Игрок: {member.mention}",
                f"🎮 Ник Minecraft: `{mc_username}`",
                f"🌍 Страна: `{actual_country_name}`",
                f"👑 Роль вайтлиста: {role_status_whitelist}",
                f"🏛️ Роль гражданина: {role_status_citizen}",
                f"🔗 RCON: `{rcon_response}`",
                f"📨 ЛС игроку: {'✅ Отправлено' if dm_sent else '❌ Не отправлено'}",
            ]

            await interaction.followup.send("\n".join(message_lines), ephemeral=True)

        except Exception as e:
            # Детальная ошибка для отладки
            error_msg = (
                f"**❌ КРИТИЧЕСКАЯ ОШИБКА:**\n"
                f"```{type(e).__name__}: {str(e)}```\n"
                f"Проверьте:\n"
                f"1. Права бота 'Управлять ролями'\n"
                f"2. Позицию роли бота в списке\n"
                f"3. ID роли: `{WHITELIST_ROLE_ID}`"
            )
            await interaction.followup.send(error_msg, ephemeral=True)
            print(f"Ошибка в AcceptButton: {e}")  # В консоль


# ========== МОДАЛЬНОЕ ОКНО ОТКАЗА ==========
class DeclineModal(Modal, title="❌ Укажите причину отказа"):
    reason = TextInput(
        label="Причина отказа",
        placeholder="Например: неверный формат анкеты",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, applicant):
        super().__init__()
        self.applicant = applicant

    async def on_submit(self, interaction: discord.Interaction):
        # Сначала отвечаем на модальное окно
        await interaction.response.defer(ephemeral=True)

        try:
            # Уведомление игрока
            try:
                embed = discord.Embed(
                    title="❌ Заявка отклонена", color=discord.Color.red()
                )
                embed.add_field(name="Причина", value=self.reason.value)
                embed.add_field(name="Администратор", value=interaction.user.mention)

                await self.applicant.send(embed=embed)
            except discord.Forbidden:
                await interaction.followup.send(
                    "⚠️ Не удалось отправить ЛС игроку", ephemeral=True
                )

            # Обновление сообщения с заявкой
            message = interaction.message
            embed = message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "❌ ЗАЯВКА ОТКЛОНЕНА"
            embed.add_field(
                name="📋 Причина", value=self.reason.value[:500], inline=False
            )
            embed.add_field(
                name="👨‍⚖️ Администратор", value=interaction.user.mention, inline=False
            )

            await message.edit(embed=embed, view=None)

            await interaction.followup.send(
                f"✅ Игроку отправлено уведомление об отказе.", ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)


# ========== КНОПКА ОТКАЗА ==========
class DeclineButton(Button):
    def __init__(self):
        super().__init__(
            label="Отказать",
            style=discord.ButtonStyle.secondary,
            custom_id="decline_btn",
            emoji="❌",
        )

    async def callback(self, interaction: discord.Interaction):
        view: AdminView = self.view

        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ У вас недостаточно прав!", ephemeral=True
            )
            return

        modal = DeclineModal(view.applicant)
        await interaction.response.send_modal(modal)


# ========== МОДАЛЬНОЕ ОКНО БАНА ==========
class BanModal(Modal, title="🔨 Укажите причину бана"):
    reason = TextInput(
        label="Причина бана",
        placeholder="Например: твинк",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, applicant, applicant_data):
        super().__init__()
        self.applicant = applicant
        self.applicant_data = applicant_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # 1. RCON бан (если нужно)
            mc_username = self.applicant_data["minecraft"]
            rcon_response = await execute_rcon_command(f"ban {mc_username}")

            # 2. Бан в Discord (опционально)
            try:
                await self.applicant.ban(
                    reason=self.reason.value[:512], delete_message_days=0
                )
                discord_ban = "✅ Забанен в Discord"
            except discord.Forbidden:
                discord_ban = "❌ Нет прав для бана в Discord"
            except Exception:
                discord_ban = "⚠️ Ошибка бана в Discord"

            # 3. Уведомление игрока
            try:
                embed = discord.Embed(
                    title="🔨 Вы забанены", color=discord.Color.dark_red()
                )
                embed.add_field(name="Причина", value=self.reason.value)
                embed.add_field(name="Администратор", value=interaction.user.mention)
                embed.add_field(name="Ник в Minecraft", value=mc_username)
                embed.add_field(
                    name="Статус",
                    value=f"Discord: {discord_ban}\nMinecraft: {rcon_response}",
                )

                await self.applicant.send(embed=embed)
            except discord.Forbidden:
                pass

            # 4. Обновление сообщения с заявкой
            message = interaction.message
            embed = message.embeds[0]
            embed.color = discord.Color.dark_red()
            embed.title = "🔨 ЗАЯВКА ЗАБАНЕНА"
            embed.add_field(
                name="📋 Причина бана", value=self.reason.value[:500], inline=False
            )
            embed.add_field(
                name="👨‍⚖️ Администратор", value=interaction.user.mention, inline=False
            )
            embed.add_field(
                name="🎮 Действие в Minecraft",
                value=f"```{rcon_response}```",
                inline=False,
            )

            await message.edit(embed=embed, view=None)

            await interaction.followup.send(
                f"✅ Игрок забанен. Причина: {self.reason.value[:100]}", ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)


# ========== КНОПКА БАНА ==========
class BanButton(Button):
    def __init__(self):
        super().__init__(
            label="Забанить",
            style=discord.ButtonStyle.danger,
            custom_id="ban_btn",
            emoji="🔨",
        )

    async def callback(self, interaction: discord.Interaction):
        view: AdminView = self.view

        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message(
                "❌ У вас нет прав на бан!", ephemeral=True
            )
            return

        modal = BanModal(view.applicant, view.applicant_data)
        await interaction.response.send_modal(modal)


# ========== КОМАНДЫ БОТА ==========
@tree.command(name="register", description="Открыть регистрацию")
@app_commands.checks.has_permissions(administrator=True)
async def register_command(interaction: discord.Interaction):
    """Команда для создания сообщения с кнопкой регистрации"""
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message(
            "❌ У вас нет прав на эту команду!", ephemeral=True
        )
        return

    view = RegistrationView()
    await interaction.response.send_message(
        "📢 **Регистрация на вайтлист открыта!**\n"
        "Нажмите кнопку ниже, чтобы подать заявку.\n"
        "⚠️ **Внимание:** Каждый игрок может зарегистрироваться только один раз!",
        view=view,
    )


@tree.command(name="toggleleader", description="Присвоить/отобрать лидерство")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(member="Участник")
async def toggle_leader(interaction, member: discord.Member):
    result = database.toggle_player_leader(member.id)
    if result["success"]:
        guild = interaction.guild
        leader_role = guild.get_role(LEADER_ROLE_ID)
        if result["new_status"]:
            await member.add_roles(leader_role)
            await interaction.response.send_message(
                f"{member.mention} теперь лидер страны {result['country']}!"
            )
        else:
            await member.remove_roles(leader_role)
            await interaction.response.send_message(
                f"{member.mention} больше не является лидером страны {result['country']}."
            )
    else:
        await interaction.response.send_message(
            "❗️ Ошибка взаимодействия", ephemeral=True
        )


@tree.command(name="createcountry", description="Создать новую страну")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    country_name="Название страны", citizen_role_id="ID роли гражданина страны"
)
async def new_country(interaction, country_name: str, citizen_role_id: str):
    try:
        role_id = int(citizen_role_id)
        result = database.create_country(country_name, role_id)
        if result:
            await interaction.response.send_message(
                f"✅ Успешно создана страна под названием **{country_name}** с ролью ID `{role_id}`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Ошибка создания страны. Возможно, страна с таким названием уже существует.",
                ephemeral=True,
            )
    except ValueError:
        await interaction.response.send_message(
            "❌ Неверный формат ID роли. ID должен быть числом.", ephemeral=True
        )


@tree.command(name="addkarma", description="Добавить/отнять стране кармы")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(
    country_name="Название страны",
    quantity="Количество кармы (отрицательное если отнять)",
)
async def add_karma(interaction, country_name: str, quantity: int):
    result = database.modify_karma_value(country_name, quantity)
    if result:
        current_karma = database.get_country_karma(country_name)
        if current_karma is not None:
            await interaction.response.send_message(
                f"✅ Количество кармы страны **{country_name}** изменено на **{quantity:+d}**. "
                f"Текущая карма: **{current_karma}**",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"✅ Карма страны **{country_name}** изменена на {quantity:+d}",
                ephemeral=True,
            )
    else:
        await interaction.response.send_message(
            f"❌ Страна '{country_name}' не найдена!", ephemeral=True
        )


@tree.command(name="karma", description="Показать карму страны")
@app_commands.describe(country_name="Название страны (необязательно)")
async def show_karma(interaction, country_name: Optional[str] = None):
    if country_name:
        # Показать карму конкретной страны
        karma = database.get_country_karma(country_name)
        if karma is not None:
            embed = discord.Embed(
                title=f"Карма страны: {country_name}",
                description=f"**{karma}** кармы",
                color=discord.Color.green() if karma >= 0 else discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"❌ Страна '{country_name}' не найдена!", ephemeral=True
            )
    else:
        # Показать топ стран
        countries = database.get_all_countries()

        if not countries:
            await interaction.response.send_message(
                "📭 В базе данных нет стран.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏆 Топ стран по карме",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )

        for i, (country_id, name, role_id, karma) in enumerate(countries[:10], 1):
            medal = ""
            if i == 1:
                medal = "🥇 "
            elif i == 2:
                medal = "🥈 "
            elif i == 3:
                medal = "🥉 "

            embed.add_field(
                name=f"{medal}{i}. {name}", value=f"📊 **{karma}** кармы", inline=False
            )

        embed.set_footer(text=f"Всего стран: {len(countries)}")
        await interaction.response.send_message(embed=embed)


@tree.command(name="countries", description="Список всех стран с информацией")
async def list_countries(interaction: discord.Interaction):
    """Показать статистику по всем странам"""
    stats = database.get_country_stats()

    if not stats:
        await interaction.response.send_message(
            "📭 В базе данных нет стран.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🌍 Все страны сервера",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for name, karma, citizens_count in stats:
        embed.add_field(
            name=f"**{name}**",
            value=f"📊 Карма: **{karma}**\n👥 Граждан: **{citizens_count}**",
            inline=True,
        )

    embed.set_footer(text=f"Всего стран: {len(stats)}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="myprofile", description="Показать ваш профиль")
async def my_profile(interaction: discord.Interaction):
    """Показать информацию о профиле игрока"""
    player_data = database.get_player(interaction.user.id)

    if not player_data:
        await interaction.response.send_message(
            "❌ Вы не зарегистрированы на сервере!", ephemeral=True
        )
        return

    reg_id, discord_id, mc_nickname, country, is_leader = player_data
    country_karma = database.get_country_karma(country)

    embed = discord.Embed(
        title=f"👤 Профиль {interaction.user.name}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(name="🎮 Minecraft ник", value=f"`{mc_nickname}`", inline=True)
    embed.add_field(name="🌍 Страна", value=country, inline=True)
    embed.add_field(
        name="👑 Статус", value="Лидер" if is_leader else "Гражданин", inline=True
    )

    if country_karma is not None:
        embed.add_field(
            name="📊 Карма страны", value=f"**{country_karma}**", inline=True
        )

    embed.set_footer(text=f"ID: {discord_id}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="checkplayer", description="Проверить, зарегистрирован ли игрок")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(member="Участник Discord")
async def check_player(interaction: discord.Interaction, member: discord.Member):
    """Проверить статус регистрации игрока"""
    if database.check_player(member.id):
        player_data = database.get_player(member.id)
        reg_id, discord_id, mc_nickname, country, is_leader = player_data

        embed = discord.Embed(
            title="✅ Игрок зарегистрирован",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Discord", value=member.mention, inline=True)
        embed.add_field(name="Minecraft ник", value=f"`{mc_nickname}`", inline=True)
        embed.add_field(name="Страна", value=country, inline=True)
        embed.add_field(
            name="Статус", value="Лидер" if is_leader else "Гражданин", inline=True
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(
            f"❌ {member.mention} не зарегистрирован на сервере.", ephemeral=True
        )


# ========== СОБЫТИЯ БОТА ==========
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} запущен!")
    print(f"📊 Серверов: {len(bot.guilds)}")

    # Синхронизация команд
    try:
        await tree.sync()
        print("✅ Слэш-команды синхронизированы")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации команд: {e}")

    # Отправка сообщения с кнопкой в канал
    channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if channel:
        try:
            # Проверяем, нет ли уже нашего сообщения
            async for msg in channel.history(limit=10):
                if msg.author == bot.user and msg.components:
                    await msg.delete()  # Удаляем старое сообщение

            # Создаем новое
            view = RegistrationView()
            embed = discord.Embed(
                title="Привет, путник!",
                description=(
                    "Добро пожаловать на Кармалис!\n"
                    "Чтобы начать игру на сервере, заполни небольшую анкету и дождись одобрения!\n\n"
                    "**⚠️ Внимание:** Каждый игрок может зарегистрироваться только **один раз**!\n"
                    "Перед тем, как подать анкету, просим тебя ознакомиться с <#1445468851591712918>ми сервера."
                ),
                color=discord.Color.blue(),
            )
            embed.set_footer(
                text="Подавая анкету, ты соглашаешься с правилами сервера."
            )

            await channel.send(embed=embed, view=view)
            print(f"✅ Сообщение отправлено в канал {channel.name}")
        except discord.Forbidden:
            print("❌ Нет прав для отправки сообщения в канал")
        except Exception as e:
            print(f"⚠️ Ошибка отправки сообщения: {e}")


# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    global database
    database = Database()
    if database is not None:
        print("БД успешно инициализирована!")
    bot.run(TOKEN)
